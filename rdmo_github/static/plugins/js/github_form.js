const {checkboxId, checkedClass, uncheckedClass} = document.currentScript.dataset

document.addEventListener('DOMContentLoaded', () => {
  if (checkboxId && checkedClass && uncheckedClass) {
    toggleRepoFields(checkboxId, checkedClass, uncheckedClass)
  }
})

function toggleRepoFields(checkboxId, checkedClass, uncheckedClass) {
  const checkBox = document.getElementById(checkboxId)
  let checkedCollection = document.getElementsByClassName(checkedClass)
  let uncheckedCollection = document.getElementsByClassName(uncheckedClass)

  if (checkBox.checked == true){
    if (! checkedCollection[0].classList.contains('show')){
      checkedCollection[0].classList.add('show')
    }

    if (! uncheckedCollection[0].classList.contains('hidden')){
      uncheckedCollection[0].classList.add('hidden')
    }

    checkedCollection[0].classList.replace('hidden', 'show')
    uncheckedCollection[0].classList.replace('show', 'hidden')

  } else {
    if (! checkedCollection[0].classList.contains('hidden')){
      checkedCollection[0].classList.add('hidden')
    }

    if (! uncheckedCollection[0].classList.contains('show')){
      uncheckedCollection[0].classList.add('show')
    }

    checkedCollection[0].classList.replace('show', 'hidden')
    uncheckedCollection[0].classList.replace('hidden', 'show')
  }
}
