function toggleRepoFields(checkbox_id, checked_collection_class, unchecked_collection_class, checked_submit_value, unchecked_submit_value) {
    const checkBox = document.getElementById(checkbox_id);
    var checkedCollection = document.getElementsByClassName(checked_collection_class);
    var uncheckedCollection = document.getElementsByClassName(unchecked_collection_class);
    var submitButton = document.getElementsByClassName('btn btn-primary');
    
    if (checkBox.checked == true){
        checkedCollection[0].style.display = 'block';
        uncheckedCollection[0].style.display = 'none';
        submitButton[0].value = checked_submit_value;
    } else {
        checkedCollection[0].style.display = 'none';
        uncheckedCollection[0].style.display = 'block';
        submitButton[0].value = unchecked_submit_value;
    }
}

function select_all_exports(choice_count) {
    var checkBox = document.getElementById('id_all_exports');

    for (let i=0; i<choice_count; i++) {
        var choice = document.getElementById(`id_exports_${i}`);
        choice.checked = checkBox.checked;
        
        var file_path_span = document.getElementById(`id_exports_file_path_${i}`);
        file_path_span.style.display = checkBox.checked ? 'flex' : 'none';

        var check_message_span = document.getElementById(`id_exports_check_message_${i}`);
        check_message_span.style.display = checkBox.checked ? 'inline' : 'none';
    }
}

function toggle_option_attributes_visibility(element) {
    const index = element.id.replace('id_exports_', '')
    
    var file_path_span = document.getElementById(`id_exports_file_path_${index}`);
    file_path_span.style.display = element.checked ? 'flex' : 'none';

    var check_message_span = document.getElementById(`id_exports_check_message_${index}`);
    check_message_span.style.display = element.checked ? 'inline' : 'none';

    var error_message_div = document.getElementById(`id_exports_errors_${index}`);
    if (error_message_div) {
      error_message_div.style.display = element.checked ? 'block' : 'none';
    }
}

function hide_check_message(element) {
    const index = element.id.replace('id_exports_text_', '')

    var duration = 1000;
    clearTimeout(element._timer);
    element._timer = setTimeout(()=>{
        var check_message_span = document.getElementById(`id_exports_check_message_${index}`);
        check_message_span.style.display = 'none';
    }, duration);
}

function hide_check_messages(element, choice_count) {
    var duration = 1000;
    clearTimeout(element._timer);
    element._timer = setTimeout(()=>{
        for (let i=0; i<choice_count; i++) {
            var choice_check_message_span = document.getElementById(`id_exports_check_message_${i}`);
            choice_check_message_span.style.display = 'none';
        }
    }, duration);
}