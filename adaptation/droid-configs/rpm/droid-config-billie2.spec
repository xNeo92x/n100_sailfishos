# Based on mer-hybris/droid-hal-configs' device spec template (GPLv2).
%define device billie2
%define vendor oneplus
%define vendor_pretty OnePlus
%define device_pretty Nord N100
%define community_adaptation 1
%define android_version_major 10
# 269 ppi / 245 ppi; initial UI choice, subject to on-device testing.
%define pixel_ratio 1.0

%include droid-configs-device/droid-configs.inc
%include patterns/patterns-sailfish-device-adaptation-billie2.inc
%include patterns/patterns-sailfish-device-configuration-billie2.inc
