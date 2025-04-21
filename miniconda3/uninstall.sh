#!/bin/bash

INSTDIR=$(cd "$(dirname "$0")" && pwd)

printf "Are you sure you want to remove %s and all of its contents?\n" "${INSTDIR}"
printf "[no] >>> "
read -r answer
answer=$(tr '[:upper:]' '[:lower:]' <<< "${answer}")
if [[ "${answer}" != "yes" ]] && [[ "${answer}" != "y" ]]; then
    printf "Aborting uninstallation\n"
    exit 2
fi

"${INSTDIR}/_conda" constructor uninstall --prefix "${INSTDIR}" "$@"
