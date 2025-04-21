#!/bin/bash

set -e

if [[ "${INSTALLER_UNATTENDED}" == "0" ]]; then
    # conda tos must fail silently for offline installations.
    # Otherwise, the installation will fail and/or users will
    # be confused by error messages.
    if [[ "${INSTALLER_TYPE}" == "PKG" ]]; then
        # Execute explicitly as user since pkg installers in
        # interactive mode execute as root.
        sudo -u "${USER}" "${PREFIX}/bin/python" -m conda tos accept --system > /dev/null 2>&1 || true
    else
        "${PREFIX}/bin/python" -m conda tos accept --system > /dev/null 2>&1 || true
    fi
fi
