from django import forms
from django.core.exceptions import ValidationError
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from rdmo_maus.forms.fields import ChoiceFieldWithOther, MultivalueCheckboxMultipleChoiceField

from .validators import validate_import_file_path, validate_new_repo_name


class GithubBaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.pop('repo_choices')
        repo_help_text = kwargs.pop('repo_help_text')
        super().__init__(*args, **kwargs)

        if repo_choices is not None:
            self.fields['repo'].choices = repo_choices

        if repo_help_text is not None:
            self.fields['repo'].help_text = repo_help_text


class GitHubExportForm(GithubBaseForm):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.get('repo_choices')
        export_choices = kwargs.pop('export_choices', None)

        if repo_choices is not None and len(repo_choices) == 0:
            export_repo_warning = _(
                'Type the URL of a GitHub repository you want to export to. You need write access '
                'to this repository, otherwise the export will fail.'
            )
            kwargs['repo_help_text'] = f'{kwargs["repo_help_text"]} {export_repo_warning}'

        super().__init__(*args, **kwargs)

        if repo_choices is not None and len(repo_choices) == 0:
            self.fields['new_repo'].initial = True

        if export_choices is not None:
            self.fields['exports'].choices = export_choices.get('choices')
            self.fields['exports'].choice_validators = export_choices.get('choice_validators', {})
            self.fields['exports'].widget.choice_attributes = export_choices.get('choice_attributes', {})
            self.fields['exports'].widget.choice_warnings = export_choices.get('choice_warnings', {})

    new_repo = forms.BooleanField(
        label=_('Create a new (public) repository'),
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                'onclick': 'toggleRepoFields("{checkbox_id}", "{checked_class}", "{unchecked_class}")'.format(
                    checkbox_id='id_new_repo',
                    checked_class='form-group field-new_repo_name',
                    unchecked_class='form-group field-repo',
                )
            }
        ),
    )

    new_repo_name = forms.CharField(
        label=_('Name for the new repository'),
        help_text=_('This name must be unique, otherwise the export will fail.'),
        required=False,
        widget=forms.TextInput(attrs={'placeholder': _('example-repo-name')}),
        validators=[validate_new_repo_name],
    )

    repo = ChoiceFieldWithOther(
        label=_('GitHub repository'),
        required=False,
    )

    exports = MultivalueCheckboxMultipleChoiceField(
        label=_('Export choices'),
        help_text=_('Warning: Existing content in GitHub will be overwritten.'),
        include_select_all_choice=True,
    )

    branch = forms.CharField(
        label=_('Branch'),
        help_text=_('An existing branch in the GitHub repository. For a new repository it must be "main".'),
        initial='main',
    )

    commit_message = forms.CharField(label=_('Commit message'))

    class Media:
        script_tag = (
            '<script src="{{}}" data-checkbox-id="{checkbox_id}" data-checked-class="{checked_class}" '
            'data-unchecked-class="{unchecked_class}" ></script>'
        ).format(
            checkbox_id='id_new_repo',
            checked_class='form-group field-new_repo_name',
            unchecked_class='form-group field-repo',
        )
        js = [format_html(script_tag, static('plugins/js/github_form.js'))]

    def clean(self):
        super().clean()
        new_repo = self.cleaned_data.get('new_repo')
        new_repo_name = self.cleaned_data.get('new_repo_name')
        repo = self.cleaned_data.get('repo')

        if new_repo and new_repo_name == '':
            self.add_error(
                'new_repo_name', ValidationError(_('A name for the new repository is required.'), code='required')
            )

        if not new_repo and 'new_repo_name' in self.errors:  # ignore new_repo_errors because repo will be used instead
            self._errors.pop('new_repo_name')

        if not new_repo and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required.'), code='required'))


class GitHubImportForm(GithubBaseForm):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.get('repo_choices')
        import_choices = kwargs.pop('import_choices', None)

        if repo_choices is not None and len(repo_choices) == 0:
            import_repo_warning = _(
                'Type the URL of a GitHub repository you want to import from. '
                'It must be either public, or accessible to you.'
            )
            kwargs['repo_help_text'] = f'{kwargs["repo_help_text"]} {import_repo_warning}'

        super().__init__(*args, **kwargs)

        if import_choices is not None:
            self.fields['imports'].choices = import_choices.get('choices')
            self.fields['imports'].choice_validators = import_choices.get('choice_validators', {})
            self.fields['imports'].widget.choice_attributes = import_choices.get('choice_attributes', {})
            self.fields['imports'].widget.choice_warnings = import_choices.get('choice_warnings', {})
        else:
            self.fields['imports'] = forms.CharField(
                label=_('File path'),
                help_text=_("The import file's relative path in the repository. The file must be in XML format."),
                widget=forms.TextInput(attrs={'placeholder': _('example_folder/example_xml_file.xml')}),
                validators=[validate_import_file_path],
            )

    repo = ChoiceFieldWithOther(
        label=_('GitHub repository'),
        required=False,
    )

    imports = MultivalueCheckboxMultipleChoiceField(
        label=_('Import choices'),
        help_text=_(
            'Select the import choices. Use the drag and drop to reorder them by priority. '
            'In the event of a conflict, choices with higher priority (at the top) '
            'will override those with lower priority (at the bottom).'
        ),
        sortable=True,
        include_select_all_choice=True,
    )

    ref = forms.CharField(label=_('Branch, tag, or commit'), initial='main')
