    # N100_EARLY_DEBUG_V1: stop before any userdata mount or switch_root.
    if [ "$(get_opt n100_debug)" = "1" ]; then
        USB_FUNCTIONS=rndis
        run_debug_session "N100 early debug: storage is not mounted"
        # 'continue' ends only the injection loop, never this debug-only boot.
        while :; do sleep 3600; done
    fi
