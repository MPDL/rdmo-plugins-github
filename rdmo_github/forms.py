import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

class GithubBaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.pop('repo_choices')
        repo_help_text = kwargs.pop('repo_help_text')
        super().__init__(*args, **kwargs)

        if repo_choices is not None:
            self.fields['repo'].widget = forms.RadioSelect(choices=repo_choices)
            
        if repo_help_text is not None:
            self.fields['repo'].help_text = repo_help_text


class ExportsChoiceMultiWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        widgets = {
            'checkbox': forms.CheckboxInput(attrs={'onchange': 'toggle_option_attributes_visibility(this)'}),
            'text': forms.TextInput(attrs={'title': _('Special characters will be percent-encoded'), 'oninput': 'hide_check_message(this)'}),
        }
        super().__init__(widgets, attrs)

    def decompress(self, value):
        boolean_value = {'False': False, 'True': True}
        if value:
            splitted_value = value.split(',')
            splitted_value[0] = boolean_value[splitted_value[0]]
            return splitted_value
        return [False, '']
    
    def get_context(self, name, value, checkbox_label, checkbox_id, text_id, attrs):
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

class ExportsChoiceMultiValueField(forms.MultiValueField):
    widget = ExportsChoiceMultiWidget

    def validate_file_path(self, value):
        min_length = 6
        max_length = 100
        pattern = f'[^A-Za-z0-9/-_. ]'
        errors = []
        
        match_obj = re.search(pattern, value)
        if match_obj is not None:
            errors.append(_('File path contains special characters. Allowed characters are alphanumeric, slash, hyphen, underscore, period, and blank space'))
        
        if len(value) > max_length:
            errors.append(_(f'File path must have at most {max_length} characters (it has {len(value)})'))

        if len(value) < min_length:
            errors.append(_(f'File path must have at least {min_length} characters (it has {len(value)})'))

        if len(errors) > 0:
            raise ValidationError(errors)

    def __init__(self):
        fields = (
            forms.BooleanField(),
            forms.CharField(validators=[self.validate_file_path]),
        )
        super().__init__(fields)

    def clean(self, value):
        """
        Validate every value in the given list. A value is validated against
        the corresponding Field in self.fields.

        For example, if this MultiValueField was instantiated with
        fields=(DateField(), TimeField()), clean() would call
        DateField.clean(value[0]) and TimeField.clean(value[1]).
        """
        clean_data = []
        errors = []
        if self.disabled and not isinstance(value, list):
            value = self.widget.decompress(value)
        if not value or isinstance(value, (list, tuple)):
            if not value or not [v for v in value if v not in self.empty_values]:
                if self.required:
                    errors.append(self.error_messages["required"])
                else:
                    return self.compress([])
        else:
            errors.append(self.error_messages["invalid"])
        for i, field in enumerate(self.fields):
            try:
                field_value = value[i]
            except IndexError:
                field_value = None
            if field_value in self.empty_values:
                if self.require_all_fields:
                    if self.required:
                        errors.append(_('A file path is required'))
                elif field.required:
                    # add an 'incomplete' error to the list of
                    # collected errors and skip field cleaning, if a required
                    # field is empty.
                    if field.error_messages["incomplete"] not in errors:
                        errors.append(field.error_messages["incomplete"])
                    continue
            try:
                clean_data.append(field.clean(field_value))
            except ValidationError as e:
                # Collect all validation errors in a single list, which we'll
                # raise at the end of clean(), rather than raising a single
                # exception for the first error we encounter. Skip duplicates.
                errors.extend(m for m in e if m not in errors)

        out = self.compress(clean_data)
        self.validate(out)
        self.run_validators(out)
        return out, errors

    def compress(self, data_list):
        return ','.join(map(str, data_list))
    
class ExportsSelectMultiple(forms.SelectMultiple):
    allow_multiple_selected = True
    option_template_name = 'plugins/exports_multivalue_select_option.html'
    template_name = 'plugins/exports_multivalue_select.html'
    add_id_index = True
    checked_attribute = {'checked': True}
    option_inherits_attrs = True
    errors = {}

    choice_widget = ExportsChoiceMultiWidget()

    def optgroups(self, name, value, attrs=None):
        'Return a list of optgroups for this widget.'
        selected_option_keys = [v.split(',')[0] for v in value]
        transformed_value = [f'{True},{v.split(",")[1]}' for v in value]
        
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
        '''
        Use an incremented id for each option where the main widget
        references the zero index.
        '''
        if id_ and self.add_id_index:
            id_ = '%s_%s' % (id_, index)
        return id_
    
    def value_from_datadict(self, data, files, name):
        value = []
        for multiwidget_name in self.choice_names:
            multiwidget_value = self.choice_widget.value_from_datadict(data, files, f'{name}_{multiwidget_name}')
            if multiwidget_value[0]:
                value.append(f'{multiwidget_name},{multiwidget_value[1]}')

        return value

class ExportsMultipleChoiceField(forms.MultipleChoiceField):
    widget = ExportsSelectMultiple
    choice_field = ExportsChoiceMultiValueField()
    _choice_names = []
    _choices_to_update = {}
    
    @property
    def choice_names(self):
        return self._choice_names
    
    @choice_names.setter
    def choice_names(self, new_names):
        self._choice_names = self.widget.choice_names = new_names

    @property
    def choices_to_update(self):
        return self._choices_to_update

    @choices_to_update.setter
    def choices_to_update(self, new_values):
        self._choices_to_update = self.widget.choices_to_update = new_values

    def clean(self, value):
        '''
        Validate the given value and return its 'cleaned' value as an
        appropriate Python object. Raise ValidationError for any errors.
        '''
        value = self.to_python(value)

        if value in self.empty_values and self.required:
            raise ValidationError(_('At least one choice must be selected'))

        for multivalue in value:
            choice_key, text_value = multivalue.split(',')
            if choice_key in self.choice_names:
                out, errors = self.choice_field.clean([True, text_value])
                if len(errors) > 0:
                    self.widget.errors[choice_key] = errors
                else:
                    self.widget.errors.pop(choice_key, None)
        if len(self.widget.errors) > 0:
            raise ValidationError(_('At least one of the selected choices is invalid'))
        
        return value

class GitHubExportForm(GithubBaseForm):
    def __init__(self, *args, **kwargs):
        export_choices = kwargs.pop('export_choices', None)
        export_choices_to_update = kwargs.pop('export_choices_to_update', None)
        super().__init__(*args, **kwargs)

        if export_choices_to_update is not None:
            self.fields['exports'].choices_to_update = export_choices_to_update
            self.fields['exports'].help_text = _('Warning: Existing content in GitHub will be overwritten. To avoid this, consider updating the file path or the branch.')

        if export_choices is not None:
            self.fields['exports'].choices = export_choices
            self.fields['exports'].choice_names = [c[1][1] for c in export_choices]
            self.fields['all_exports'].widget = forms.CheckboxInput(
                attrs={'onclick': f'select_all_exports({len(export_choices)})'}
            )

    new_repo = forms.BooleanField (
        label=_('Create new repository'),
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                'onclick': f'''toggleRepoFields("id_new_repo", "form-group field-new_repo_name", "form-group field-repo", "{_('Export to GitHub')}", "{_('Proceed')}")'''
        })
    )
    new_repo_name = forms.CharField(
        label=_('Name for the new repository'),
        required=False
    )
    repo = forms.CharField(
        label=_('GitHub repository'),
        required=False,
        help_text=_('Please use the form username/repository or organization/repository.')
    )

    exports = ExportsMultipleChoiceField(
        label=_('Export choices'),
        help_text=_('Warning: Existing content in GitLab will be overwritten'),
    )

    all_exports = forms.BooleanField(
        label=_('Select all export choices'),
        required=False,
    )

    branch = forms.CharField(label=_('Branch'), initial='main')

    commit_message = forms.CharField(label=_('Commit message'))

    def clean(self):
        super().clean()
        new_repo = self.cleaned_data.get('new_repo')
        new_repo_name = self.cleaned_data.get('new_repo_name')
        repo = self.cleaned_data.get('repo')

        if new_repo and new_repo_name == '':
            self.add_error('new_repo_name', ValidationError(_('A name for the new repository is required')))
        
        if not new_repo and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required')))


class GitHubImportForm(GithubBaseForm):
    other_repo_check = forms.BooleanField (
        label=_('Not my repository'),
        help_text=_('Check if the repository you want to import from is not yours'),
        required=False,
        widget=forms.CheckboxInput(attrs={'onclick': 'toggleRepoFields("id_other_repo_check", "form-group field-other_repo", "form-group field-repo")'})
    )
    repo = forms.CharField(
        label=_('GitHub repository'),
        help_text=_('Please use the form username/repository or organization/repository.'),
        required=False
    )
    other_repo = forms.CharField(
        label=_('Public GitHub repository'),
        help_text=_('Link of the public GitHub repository you want to import from'),
        required=False
    )
    path = forms.CharField(label=_('File path'))
    ref = forms.CharField(label=_('Branch, tag, or commit'), initial='main')

    def clean(self):
        super().clean()
        other_repo_check = self.cleaned_data.get('other_repo_check')
        other_repo = self.cleaned_data.get('other_repo')
        repo = self.cleaned_data.get('repo')

        if other_repo_check and other_repo == '':
            self.add_error('other_repo', ValidationError(_('A repository link is required')))
        
        if not other_repo_check and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required')))