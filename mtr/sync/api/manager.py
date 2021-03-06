from __future__ import unicode_literals

from collections import OrderedDict

from django.utils.six.moves import filterfalse

from .exceptions import ItemAlreadyRegistered, ItemDoesNotRegistered
from .helpers import column_value, make_model_class, model_settings, \
    process_attribute
from ..settings import IMPORT_PROCESSORS


class ProcessorManagerMixin(object):

    def convert_value(self, value, model, field, export=False, action=None):
        """Process value with filters and actions"""

        convert_action = 'export' if export else 'import'

        for convert_func in field.ordered_converters:
            value = convert_func(value, model, field, convert_action)

        return value

    def processor_choices(self):
        """Return all registered processors"""

        for name, processor in self.processors.items():
            yield (
                name,
                '{} | {}'.format(
                    processor.file_format,
                    processor.file_description))

    def dataset_choices(self):
        """Return all registered data sources"""

        for name, dataset in self.datasets.items():
            yield(name, getattr(dataset, 'label', dataset.__name__))

    def action_choices(self):
        """Return all registered actions"""

        for name, action in self.actions.items():
            yield(name, getattr(action, 'label', action.__name__))

    def make_processor(self, settings, from_extension=False):
        """Create new processor instance if exists"""

        processor = None

        if from_extension:
            extension = settings.buffer_file.path.split('.')[-1]
            for pr in self.processors.values():
                if pr.file_format.strip('.') == extension:
                    processor = pr
                    settings.processor = processor.__name__
                    break

        processor = self.get_or_raise('processor', settings.processor)

        return processor(settings, self)

    def export_data(self, settings, data=None):
        """Export data to file if no data passed,
        create queryset it from settings"""

        processor = self.make_processor(settings)

        if data is None:
            queryset = self.prepare_export_queryset(settings)
            data = self.prepare_export_data(processor, queryset)

        return processor.export_data(data)

    def prepare_export_queryset(self, settings):
        current_model = make_model_class(settings)

        if settings.dataset:
            dataset = self.get_or_raise('dataset', settings.dataset)
            dataset = dataset(current_model, settings)
        else:
            dataset = current_model.objects.all()

        return dataset

    def prepare_export_data(self, processor, queryset):
        """Prepare data using filters from settings
        and return data with dimensions"""

        settings = processor.settings
        model = make_model_class(settings)
        msettings = model_settings(model)

        exclude = msettings.get('exclude', [])

        fields = settings.fields_with_processors()
        fields = list(filterfalse(
            lambda f: f.attribute in exclude, fields))

        if settings.end_row:
            rows = settings.end_row
            if settings.start_row:
                rows -= settings.start_row
                rows += 1

            queryset = queryset[:rows]

        if settings.end_col:
            cols = column_value(settings.end_col)
            if settings.start_col:
                cols -= column_value(settings.start_col)
                cols += 1

            fields = fields[:cols]

        return {
            'rows': queryset.count(),
            'cols': len(fields),
            'fields': fields,
            'items': (
                self.convert_value(
                    process_attribute(item, field.attribute),
                    model, field, export=True)
                for item in queryset
                for field in fields
            )
        }

    def import_data(self, settings, path=None):
        """Import data to database"""

        processor = self.make_processor(settings)
        model = make_model_class(settings)

        return processor.import_data(model, path)

    def model_data(self, processor, model, fields):
        for row_index in processor.rows:
            _model = {}
            row = processor.read(row_index)

            for index, field in enumerate(fields):
                col = column_value(field.name) if field.name else index
                value = self.convert_value(row[col], model, field)
                _model[field.attribute] = value

            yield row_index, _model

    def prepare_import_data(self, processor, model):
        """Prepare data using filters from settings and return iterator"""

        settings = processor.settings
        fields = list(settings.fields_with_processors())

        if settings.end_col:
            cols = column_value(settings.end_col)
            if settings.start_col:
                cols -= column_value(settings.start_col)
                cols += 1

            fields = fields[:cols]

        return {
            'cols': len(fields),
            'items': self.model_data(processor, model, fields),
        }

    def import_processors_modules(self):
        """Import modules within IMPORT_PROCESSORS paths"""

        for module in IMPORT_PROCESSORS():
            __import__(module)

        __import__('mtr.sync.api.converters')
        __import__('mtr.sync.api.actions')


class Manager(ProcessorManagerMixin):

    """Manager for data processors"""

    def __init__(self):
        self.processors = OrderedDict()
        self.actions = OrderedDict()
        self.converters = OrderedDict()
        self.datasets = OrderedDict()

    def _make_key(self, key):
        return '{}s'.format(key)

    def get_or_raise(self, name, key):
        value = getattr(self, self._make_key(name), {})
        value = value.get(key, None)

        if value is None:
            raise ItemDoesNotRegistered(
                '{} not registered at {}'.format(key, name))

        return value

    def has(self, name, key):
        for value in getattr(self, self._make_key(name), {}).values():
            if value == key:
                return True
        return False

    def _register_dict(self, type_name, func_name, label, **kwargs):
        """Return decorator for adding functions as key, value to dict
        and send manager_registered signal to handle new params"""

        def decorator(func):
            key = self._make_key(type_name)
            values = getattr(self, key, OrderedDict())
            position = getattr(func, 'position', 0)
            new_name = func_name or func.__name__
            if label:
                func.label = label

            if values is not None:
                if values.get(new_name, None) is not None:
                    raise ItemAlreadyRegistered(
                        '{} already registred at {}'.format(new_name, key))

                values[new_name] = func
                if position:
                    values = OrderedDict(
                        sorted(
                            values.items(),
                            key=lambda p: getattr(p[1], 'position', 0)))
                setattr(self, key, values)

            return func

        return decorator

    def register(self, type_name, item=None, name=None, label=None, **kwargs):
        """Decorator and function to config new processors, handlers"""

        func = self._register_dict(type_name, name, label, **kwargs)

        return func(item) if item else func

    def unregister(self, type_name, item=None):
        """Decorator to pop dict items"""

        items = getattr(self, self._make_key(type_name), None)
        if items is not None:
            items.pop(getattr(item, '__name__', item), None)

        return item

manager = Manager()
manager.import_processors_modules()
