function toggleRepoFields(checkbox_id, checked_collection_class, unchecked_collection_class) {
    const checkBox = document.getElementById(checkbox_id);
    var checkedCollection = document.getElementsByClassName(checked_collection_class);
    var uncheckedCollection = document.getElementsByClassName(unchecked_collection_class);
    
    if (checkBox.checked == true){
        checkedCollection[0].style.display = "block";
        uncheckedCollection[0].style.display = "none";
    } else {
        checkedCollection[0].style.display = "none";
        uncheckedCollection[0].style.display = "block";
    }
}