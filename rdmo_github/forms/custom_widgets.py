from django import forms
from django.utils.translation import gettext_lazy as _

class MultivalueCheckboxWidget(forms.MultiWidget):
    def __init__(self, simple_checkbox=False, attrs=None):
        widgets = {
            'checkbox': forms.CheckboxInput()
        }

        if not simple_checkbox:
            widgets['text'] = forms.TextInput()

        super().__init__(widgets, attrs)

    def decompress(self, value):
        '''Transform input value to a list with the correctly typed value for each subwidget:
            - a boolean value for the checkbox
            - a string value for the text
        '''
        # print('decompress()')
        # print(f'value: {value}')
        boolean_value = {'False': False, 'True': True}
        if isinstance(value, str):
            splitted_value = value.split(',')
            splitted_value[0] = boolean_value[splitted_value[0]]
            return splitted_value
        
        return [False, '']
    
    def get_context(self, name, value, checkbox_label, text_label, checkbox_id, text_id, attrs, extra_attrs=None):
        '''Create context for MultivalueCheckboxMultipleChoiceWidget.option_template_name. '''
        
        context = super().get_context(name, value, attrs)
        # value is a list/tuple of values, each corresponding to a widget
        # in self.widgets.
        if not isinstance(value, (list, tuple)):
            value = self.decompress(value)

        final_attrs = context['widget']['attrs']
        subwidgets = []
        for i, (widget_name, widget) in enumerate(
            zip(self.widgets_names, self.widgets)
        ):
            try:
                widget_value = value[i]
            except IndexError:
                widget_value = None
            
            extra_widget_attrs = extra_attrs.get(widget_name.strip('_'), {}) if isinstance(extra_attrs, dict) else {}

            widget_attrs = final_attrs.copy()
            widget_attrs.update(extra_widget_attrs)
            if widget_name == '_text':
                widget_attrs.update({'id': text_id, 'class': 'form-control', 'style': 'flex-grow:1;'})
            if widget_name == '_checkbox':
                widget_attrs.update({'id': checkbox_id})
            
            widget_context = widget.get_context(name + widget_name, widget_value, widget_attrs)['widget']
            if widget_name == '_text':
                widget_context.update({'label': text_label})
            if widget_name == '_checkbox':
                widget_context.update({'label': checkbox_label})
            subwidgets.append(widget_context)
        
        context['widget']['subwidgets'] = subwidgets
        return context
    

class MultivalueCheckboxMultipleChoiceWidget(forms.SelectMultiple):
    option_inherits_attrs = True
    errors = {}
    _choice_keys = []
    _choice_widgets = {}
    _choice_attributes = {}
    option_template_name = 'plugins/custom_multivalue_select_option.html'
    template_name = 'plugins/custom_multivalue_select.html'

    def __init__(self, *, sortable=False, **kwargs):
        super().__init__(**kwargs)

        self.sortable = sortable

    @property
    def choices(self):
        return self._choices
    
    @choices.setter
    def choices(self, new_choices):
        self._choices = new_choices
        self.choice_widgets = new_choices

    @property
    def choice_widgets(self):
        return self._choice_widgets
    
    @choice_widgets.setter
    def choice_widgets(self, new_choices):
        new_choice_widgets = {}
        for c in new_choices:
            simple_checkbox = False
            
            values = c[0].split(',')
            choice_key = c[2]
            if isinstance(values, list) and len(values) == 1:
                simple_checkbox = True

            new_choice_widgets[choice_key] = MultivalueCheckboxWidget(simple_checkbox=simple_checkbox)

        self._choice_widgets = new_choice_widgets
    
    @property
    def choice_keys(self):
        return self._choice_keys
    
    @choice_keys.setter
    def choice_keys(self, new_keys):
        self._choice_keys = new_keys

    @property
    def choice_attributes(self):
        return self._choice_attributes
    
    @choice_attributes.setter
    def choice_attributes(self, new_attributes):
        self._choice_attributes = new_attributes

    def sort_choices(self, data, name):
        selected_choices = [k for k,v in data.items() if (k.startswith(name) and k.endswith('_checkbox') and 'on' in v)]
        sorted_choice_keys = [c.removeprefix(f'{name}_').removesuffix('_checkbox') for c in selected_choices]

        for k in self.choice_keys:
            if k not in sorted_choice_keys:
                sorted_choice_keys.append(k)

        sorted_choices = []
        for k in sorted_choice_keys:
            choice = next((c for c in self.choices if c[2] == k), None)
            if choice is not None:
                sorted_choices.append(choice)

        return sorted_choice_keys, sorted_choices

    def optgroups(self, name, value, attrs=None):
        '''Return a list of choices for this widget.
        Each choice consists of a multi widget with a checkbox and a text.
        '''
        
        transformed_value = []
        selected_option_keys = []
        for v in value:
            v_list = v.split(',')
            selected_option_keys.append(v_list[0])
            transformed_v = f'True,{v_list[1]}' if len(v_list) > 1 else 'True'
            transformed_value.append(transformed_v)

        current_errors = {k: v for k, v in self.errors.items() if k in selected_option_keys}
        self.errors = current_errors

        groups = []
        for index, (option_value, option_labels, option_key) in enumerate(self.choices):
            i = selected_option_keys.index(option_key) if option_key in selected_option_keys else None
            option_value = transformed_value[i] if i is not None else option_value
            choice_widget = self.choice_widgets[option_key]
            decompressed_option_value = choice_widget.decompress(option_value)
            
            selected = self.allow_multiple_selected and decompressed_option_value[0]
            
            option_name = f'{name}_{option_key}'
            
            groups.append(self.create_option(
                choice_widget,
                option_name,
                option_value,
                option_labels,
                option_key,
                selected,
                index,
                attrs=attrs,
            ))

        return groups

    def create_option(
        self, widget, name, value, labels, key, selected, index, attrs=None
    ):
        '''Create a choice consisting of a multi widget with a checkbox and a text. '''  
        
        index = str(index)
        option_attrs = (
            self.build_attrs(self.attrs, attrs) if self.option_inherits_attrs else {}
        )

        checkbox_id = text_id = None
        if 'id' in option_attrs:
            checkbox_id = '%s_%s' % (option_attrs['id'], index)
            text_id = '%s_%s' % (f'{option_attrs["id"]}_text', index)
            option_attrs = {}
        
        if selected:
            option_attrs.update(self.checked_attribute)

        checkbox_label = text_label = ''
        if isinstance(labels, tuple):
            checkbox_label = labels[0]
            text_label = labels[1] if len(labels) > 1 else ''
        else:
            checkbox_label = labels

        extra_option_attrs = self.choice_attributes.get(key)
        option_context = widget.get_context(name, value, checkbox_label, text_label, checkbox_id, text_id, option_attrs, extra_option_attrs)
        choices_to_update = getattr(self, 'choices_to_update', None)
        option_in_repo = self.choices_to_update[key] if choices_to_update and key in self.choices_to_update.keys() else None
        option_errors = self.errors[key] if key in self.errors.keys() else None
        
        return {
            'name': name,
            'value': value,
            'errors': option_errors,
            'subwidgets': option_context['widget']['subwidgets'],
            'selected': selected,
            'option_in_repo': option_in_repo,
            'template_name': self.option_template_name,
        }
    
    def value_from_datadict(self, data, files, name):
        if self.sortable:
            self.choice_keys, self.choices = self.sort_choices(data, name)
        
        value = []
        for multiwidget_name in self.choice_keys:
            choice_widget = self.choice_widgets[multiwidget_name]
            multiwidget_value = choice_widget.value_from_datadict(data, files, f'{name}_{multiwidget_name}')
            
            if multiwidget_value[0]:
                choice_value = multiwidget_name if len(multiwidget_value) == 1 else f'{multiwidget_name},{multiwidget_value[1]}'
                value.append(choice_value)

        return value
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['sortable'] = self.sortable
        return context