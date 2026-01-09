from django import forms
from django.utils.translation import gettext_lazy as _

class CustomChoiceMultiWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        widgets = {
            'checkbox': forms.CheckboxInput(attrs={'onchange': 'toggle_option_attributes_visibility(this)'}),
            'text': forms.TextInput(attrs={
                'title': _('Special characters will be percent-encoded'), 
                'placeholder': _('example_folder/example_file.extension'),
                'oninput': 'hide_check_message(this)'
            })
        }
        super().__init__(widgets, attrs)

    def decompress(self, value):
        '''Transform input value to a list with the correctly typed value for each subwidget:
            - a boolean value for the checkbox
            - a string value for the text
        '''
         
        boolean_value = {'False': False, 'True': True}
        if isinstance(value, str):
            splitted_value = value.split(',')
            splitted_value[0] = boolean_value[splitted_value[0]]
            return splitted_value
        
        return [False, '']
    
    def get_context(self, name, value, checkbox_label, checkbox_id, text_id, attrs):
        '''Create context for CustomSelectMultiple.option_template_name. '''
        
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
            
            widget_attrs = final_attrs.copy()
            if widget_name == '_text':
                widget_attrs.update({'id': text_id, 'style': 'flex-grow:1;'})
            if widget_name == '_checkbox':
                widget_attrs.update({'id': checkbox_id})
            
            widget_context = widget.get_context(name + widget_name, widget_value, widget_attrs)['widget']
            if widget_name == '_checkbox':
                widget_context.update({'label': checkbox_label})
            subwidgets.append(widget_context)
        
        context['widget']['subwidgets'] = subwidgets
        return context
    

class CustomSelectMultiple(forms.SelectMultiple):
    allow_multiple_selected = True
    add_id_index = True
    checked_attribute = {'checked': True}
    option_inherits_attrs = True
    errors = {}
    _choice_names = []

    choice_widget = CustomChoiceMultiWidget()

    def __init__(self, *args, **kwargs):
        sortable = kwargs.pop('sortable', False)
        super().__init__(*args, **kwargs)

        self.sortable = sortable
        self.option_template_name = 'plugins/sortable_custom_multivalue_select_option.html' if sortable else 'plugins/custom_multivalue_select_option.html'
        self.template_name = 'plugins/sortable_custom_multivalue_select.html' if sortable else 'plugins/custom_multivalue_select.html'

    @property
    def choice_names(self):
        return self._choice_names
    
    @choice_names.setter
    def choice_names(self, new_names):
        self._choice_names = new_names

    def sort_choices(self, data, name):
        selected_choices = [k for k,v in data.items() if (k.startswith(name) and 'on' in v)]
        sorted_choice_names = [c.removeprefix(f'{name}_').removesuffix('_checkbox') for c in selected_choices]

        for k in self.choice_names:
            if k not in sorted_choice_names:
                sorted_choice_names.append(k)

        sorted_choices = []
        for k in sorted_choice_names:
            choice = next((c for c in self.choices if c[1][1] == k), None)
            if choice is not None:
                sorted_choices.append(choice)

        return sorted_choice_names, sorted_choices

    def optgroups(self, name, value, attrs=None):
        '''Return a list of choices for this widget.
        Each choice consists of a multi widget with a checkbox and a text.
        '''
        print(f'value: {value}')
        selected_option_keys = [v.split(',')[0] for v in value]
        print(f'selected_option_keys: {selected_option_keys}')
        transformed_value = [f'{True},{v.split(",")[-1]}' for v in value]
        print(f'transformed_value: {transformed_value}')

        current_errors = {k: v for k, v in self.errors.items() if k in selected_option_keys}
        self.errors = current_errors

        groups = []
        for index, (option_value, (option_label, option_key)) in enumerate(self.choices):
            i = selected_option_keys.index(option_key) if option_key in selected_option_keys else None
            option_value = transformed_value[i] if i is not None else option_value
            decompressed_option_value = self.choice_widget.decompress(option_value)
            
            selected = self.allow_multiple_selected and decompressed_option_value[0]
            
            option_name = f'{name}_{option_key}'
            
            groups.append(self.create_option(
                self.choice_widget,
                option_name,
                option_value,
                option_label,
                option_key,
                selected,
                index,
                attrs=attrs,
            ))

        return groups

    def create_option(
        self, widget, name, value, label, key, selected, index, attrs=None
    ):
        '''Create a choice consisting of a multi widget with a checkbox and a text. '''  
        
        index = str(index)
        option_attrs = (
            self.build_attrs(self.attrs, attrs) if self.option_inherits_attrs else {}
        )
        if 'id' in option_attrs:
            checkbox_id = self.id_for_label(option_attrs['id'], index)
            text_id = self.id_for_label(f'{option_attrs["id"]}_text', index)
            option_attrs = {}
        if selected:
            option_attrs.update(self.checked_attribute)
        option_context = widget.get_context(name, value, label, checkbox_id, text_id, option_attrs)
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
    
    def id_for_label(self, id_, index='0'):
        '''Use an incremented id for each option where the main widget
        references the zero index.
        '''
        
        if id_ and self.add_id_index:
            id_ = '%s_%s' % (id_, index)
        return id_
    
    def value_from_datadict(self, data, files, name):
        # # print('CustomSelectMultiple.value_from_datadict()')
        # # print('data')
        # # print(data)
        # # print('self.sortable')
        # # print(f'name: {name}')
        # # print(f'self.choice_names: {self.choice_names}')

        if self.sortable:
            self.choice_names, self.choices = self.sort_choices(data, name)
        
        value = []
        for multiwidget_name in self.choice_names:
            multiwidget_value = self.choice_widget.value_from_datadict(data, files, f'{name}_{multiwidget_name}')
            if multiwidget_value[0]:
                value.append(f'{multiwidget_name},{multiwidget_value[1]}')

        return value