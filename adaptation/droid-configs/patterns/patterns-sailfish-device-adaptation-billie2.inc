# Initial package selection, based on the mer-hybris template (GPLv2).
%package -n patterns-sailfish-device-adaptation-billie2
Summary: Experimental OnePlus Nord N100 hardware adaptation
Requires: droid-hal-billie2
Requires: droid-hal-billie2-img-boot
Requires: droid-hal-billie2-kernel-modules
Requires: droid-config-billie2-sailfish
Requires: droid-config-billie2-pulseaudio-settings
Requires: droid-config-billie2-policy-settings
Requires: droid-config-billie2-preinit-plugin
Requires: droid-config-billie2-flashing
Requires: droid-config-billie2-bluez5
Requires: droid-hal-version-billie2
Requires: libhybris-libEGL
Requires: libhybris-libGLESv2
Requires: libhybris-libwayland-egl
Requires: qt5-qtwayland-wayland_egl
Requires: qt5-qpa-hwcomposer-plugin
Requires: qtscenegraph-adaptation
Requires: qt5-plugin-generic-evdev
Requires: hybris-libsensorfw-qt5
Requires: bluebinder
Requires: ofono-binder-plugin
Requires: dummy_netd
Requires: pulseaudio-modules-droid
Requires: pulseaudio-modules-droid-hidl
Requires: qt5-qtmultimedia-plugin-mediaservice-gstmediacapture
Requires: audiosystem-passthrough-dummy-af
Requires: gstreamer1.0-droid
Requires: usb-moded
Requires: rfkill
Requires: jolla-devicelock-daemon-encsfa
Requires: geoclue-provider-hybris

# Vibra/fingerprint/VoLTE need hardware evidence before adding integration.
%description -n patterns-sailfish-device-adaptation-billie2
Experimental Android 13 HAL with Android 11 firmware baseline. Hardware operation is unverified.

%files -n patterns-sailfish-device-adaptation-billie2
