from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .custom_fields import ExportsMultipleChoiceField
from .custom_validators import validate_new_repo_name

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
            self.fields['branch'].widget = forms.TextInput(attrs={'oninput': f'hide_check_messages(this, {len(export_choices)})'})

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
        help_text=_('Unique name for the new repository. No other of your repositories may have the same name, otherwise the export will fail.'),
        required=False,
        validators=[validate_new_repo_name]
    )

    repo = forms.ChoiceField(
        label=_('GitHub repository'),
        required=False,
        widget=forms.RadioSelect
    )

    exports = ExportsMultipleChoiceField(
        label=_('Export choices'),
        help_text=_('Warning: Existing content in GitHub will be overwritten.'),
    )

    all_exports = forms.BooleanField(
        label=_('Select all export choices'),
        required=False,
    )

    branch = forms.CharField(
        label=_('Branch'), 
        help_text=_('An existing branch in the GitHub repository. For a new repository it must be the default branch "main".'),
        initial='main'
    )

    commit_message = forms.CharField(label=_('Commit message'))

    def clean(self):
        super().clean()
        new_repo = self.cleaned_data.get('new_repo')
        new_repo_name = self.cleaned_data.get('new_repo_name')
        repo = self.cleaned_data.get('repo')

        if new_repo and new_repo_name == '':
            self.add_error('new_repo_name', ValidationError(_('A name for the new repository is required.')))
        
        if not new_repo and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required.')))


class GitHubImportForm(GithubBaseForm):
    other_repo_check = forms.BooleanField (
        label=_('Use other repository'),
        required=False,
        widget=forms.CheckboxInput(attrs={'onclick': 'toggleRepoFields("id_other_repo_check", "form-group field-other_repo", "form-group field-repo")'})
    )

    repo = forms.ChoiceField(
        label=_('GitHub repository'),
        required=False,
        widget=forms.RadioSelect
    )

    other_repo = forms.CharField(
        label=_('GitHub repository'),
        help_text=_("URL of GitHub repository you want to import from. If this repository is not public, you must have access to it."),
        required=False
    )

    path = forms.CharField(
        label=_('File path'),
        help_text=_("The import file's relative path in the repository. The file must be in XML format.")
    )

    ref = forms.CharField(
        label=_('Branch, tag, or commit'), 
        initial='main'
    )

    def clean(self):
        super().clean()
        other_repo_check = self.cleaned_data.get('other_repo_check')
        other_repo = self.cleaned_data.get('other_repo')
        repo = self.cleaned_data.get('repo')

        if other_repo_check and other_repo == '':
            self.add_error('other_repo', ValidationError(_('A GitHub repository is required.'), code='required'))
        
        if not other_repo_check and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required.'), code='required'))