from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .utils import get_optionset_elements_with_uri

class GithubBaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.pop('repo_choices')
        repo_help_text = kwargs.pop('repo_help_text')
        super().__init__(*args, **kwargs)

        if repo_choices is not None:
            self.fields['repo'].widget = forms.RadioSelect(choices=repo_choices)
            
        if repo_help_text is not None:
            self.fields['repo'].help_text = repo_help_text


class GitHubExportForm(GithubBaseForm):
    new_repo = forms.BooleanField (
        label=_('Create new repository'),
        required=False,
        widget=forms.CheckboxInput(attrs={'onclick': 'toggleRepoFields("id_new_repo", "form-group field-new_repo_name", "form-group field-repo")'})
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
    export_choices = get_optionset_elements_with_uri('https://dev-rdmo.mpdl.mpg.de/terms/options/github_export')
    export_options = forms.CharField(
        label=_('Export options'),
        help_text=_('Warning: Existing content in GitHub will be overwritten'),
        widget=forms.CheckboxSelectMultiple(
            choices=export_choices
        )
    )
    all_export_options = forms.BooleanField (
        label=_('Select all export options'),
        required=False,
        widget=forms.CheckboxInput(attrs={'onclick': f'select_all_export_options({len(export_choices)})'})
    )
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