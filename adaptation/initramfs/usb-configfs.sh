# SPDX-License-Identifier: GPL-2.0-only
# N100 configfs debug gadget. The kernel provides rndis.usb0, not rndis_bam.
usb_setup_configfs() {
    n100_gadget="$GADGET_DIR/g1"
    G_USB_ISERIAL="$n100_gadget/strings/0x409/serialnumber"
    if [ -e "$n100_gadget/UDC" ]; then
        echo "" > "$n100_gadget/UDC"
    fi
    mkdir -p "$n100_gadget/strings/0x409"
    mkdir -p "$n100_gadget/configs/c.1/strings/0x409"
    write "$n100_gadget/idVendor" "0x18D1"
    write "$n100_gadget/idProduct" "0xD001"
    write "$G_USB_ISERIAL" "$1"
    write "$n100_gadget/strings/0x409/manufacturer" "Mer Boat Loader"
    write "$n100_gadget/strings/0x409/product" "$CUSTOMPRODUCT"
    write "$n100_gadget/configs/c.1/strings/0x409/configuration" "rndis"
    mkdir -p "$n100_gadget/functions/rndis.usb0"
    if [ ! -L "$n100_gadget/configs/c.1/rndis.usb0" ]; then
        ln -s "$n100_gadget/functions/rndis.usb0" "$n100_gadget/configs/c.1/rndis.usb0"
    fi

    udc="$(get_opt androidboot.usbcontroller | head -1)"
    if [ -z "$udc" ] || [ ! -d "/sys/class/udc/$udc" ]; then
        udc=""
        for udc_path in /sys/class/udc/*; do
            [ -d "$udc_path" ] || continue
            case "$udc_path" in
                *dummy) continue ;;
            esac
            udc="${udc_path##*/}"
            break
        done
    fi
    if [ -z "$udc" ]; then
        echo "N100 debug: no real UDC available" >> /diagnosis.log
        return 1
    fi
    n100_mode="/sys/class/udc/$udc/device/../mode"
    if [ -w "$n100_mode" ]; then
        write "$n100_mode" "peripheral"
    fi
    echo "$udc" > "$n100_gadget/UDC"
}

usb_setup() {
    if [ ! -d "$GADGET_DIR" ]; then
        echo "N100 debug: configfs gadget directory is unavailable" >> /diagnosis.log
        return 1
    fi
    usb_setup_configfs "$1"
}
