# Based on mer-hybris/droid-hal-device's device spec template (BSD).
%define device billie2
%define vendor oneplus
%define vendor_pretty OnePlus
%define device_pretty Nord N100
%define droid_target_aarch64 1

# Logical Android partitions require device-mapper setup before they can mount.
# Do not generate physical /dev/block/by-name/{system,vendor,product} mounts.
# Bring-up remains blocked until the mounting strategy in docs/BRINGUP.md passes.
%define makefstab_skip_entries / /system /system_ext /vendor /product /odm /metadata /linkerconfig /dev/stune /dev/cpuset /dev/cpuctl

%define android_config \
#define WANT_ADRENO_QUIRKS 1\
%{nil}

# Clean obsolete Android root aliases and stage the explicitly built runtime inputs.
%define custom_install_cmds bash rpm/helpers/remove-android-root-aliases.sh "%{buildroot}" && bash rpm/helpers/install-android-runtime.sh "%{android_root}" "%{buildroot}"

# Intentionally no installable_zip or automatic partition-update macros yet.
%include rpm/dhd/droid-hal-device.inc
