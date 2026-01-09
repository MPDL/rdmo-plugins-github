function toggleRepoFields(checkbox_id, checked_collection_class, unchecked_collection_class) {
    const checkBox = document.getElementById(checkbox_id);
    var checkedCollection = document.getElementsByClassName(checked_collection_class);
    var uncheckedCollection = document.getElementsByClassName(unchecked_collection_class);
    
    if (checkBox.checked == true){
        checkedCollection[0].style.display = 'block';
        uncheckedCollection[0].style.display = 'none';
    } else {
        checkedCollection[0].style.display = 'none';
        uncheckedCollection[0].style.display = 'block';
    }
}

function toggle_option_attributes_visibility(element) {
    const index = element.id.replace('id_imports_', '')
    
    var file_path_span = document.getElementById(`id_imports_file_path_${index}`);
    file_path_span.style.display = element.checked ? 'flex' : 'none';

    var check_message_span = document.getElementById(`id_imports_check_message_${index}`);
    check_message_span.style.display = element.checked ? 'inline' : 'none';

    var error_message_div = document.getElementById(`id_imports_errors_${index}`);
    if (error_message_div) {
      error_message_div.style.display = element.checked ? 'block' : 'none';
    }

    const selectedZone = document.getElementById('selected_id_imports');
    const unselectedZone = document.getElementById('id_imports');
    const curTask = document.getElementById(index);
    
    if (element.checked == true) {
        selectedZone.appendChild(curTask);
        curTask.draggable = true;
    } else {
        unselectedZone.appendChild(curTask);
    }
}

const draggables = document.querySelectorAll(".choice-block");
const droppables = document.querySelectorAll(".selected");
// console.log(droppables)
var choice_id= '0';
var emp_id = '0';


draggables.forEach((choice) => {
  choice.addEventListener("dragstart", () => {
    choice.classList.add("is-dragging");
  });
  choice.addEventListener("dragend", () => {
    choice.classList.remove("is-dragging");
  });
});

droppables.forEach((zone) => {
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();

    const bottomTask = insertAboveTask(zone, e.clientY);
    const curTask = document.querySelector(".is-dragging");

    if (!bottomTask) {
      zone.appendChild(curTask);
    } else {
      zone.insertBefore(curTask, bottomTask);
    }
    
    choice_id = curTask.id;
    emp_id = zone.id;

    const checkBox = document.getElementById(`id_imports_${choice_id}`);
    checkBox.checked = true;
    /*toggle_option_attributes_visibility(checkBox);*/
    });
});

const insertAboveTask = (zone, mouseY) => {
  const els = zone.querySelectorAll(".choice-block:not(.is-dragging)");
  
  let closestTask = null;
  let closestOffset = Number.NEGATIVE_INFINITY;


  els.forEach((choice) => {
    const { top } = choice.getBoundingClientRect();

    const offset = mouseY - top;

    if (offset < 0 && offset > closestOffset) {
      closestOffset = offset;
      closestTask = choice;
    }
  });

  return closestTask;
};