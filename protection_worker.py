import time
import protection

CHECK_INTERVAL = 5


def main():
    print("Protection worker started.")
    print(f"Checking protected apps every {CHECK_INTERVAL}s.")

    while True:
        config = protection.load_config()

        if config.get("enabled", True):
            apps = config.get("apps", {})

            for process_name, app_config in apps.items():
                if not isinstance(app_config, dict):
                    continue

                if not app_config.get("enabled", False):
                    continue

                if protection.is_limit_reached(process_name):
                    if protection.close_process(process_name):
                        limit = protection.get_limit_seconds(process_name)
                        print(
                            f"Protection: {process_name} reached "
                            f"{limit / 60:.0f} minute limit. App closed."
                        )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProtection worker stopped.")