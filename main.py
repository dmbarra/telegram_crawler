"""Entry point. Run from project root: python main.py"""
import asyncio
from colorama import Fore
from telethon import TelegramClient
from src.config import API_ID, API_HASH, SESSION_NAME, validate
from src.storage import load_state, save_channels_to_env
from src.runner import fetch_channels, print_channels, build_filter, run_single, run_all


async def main():
    if not validate():
        return

    state = load_state()
    if state["downloaded"]:
        print(f"{Fore.YELLOW}Loaded dedup state: "
              f"{len(state['downloaded'])} files tracked.{Fore.RESET}")

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        print(f"{Fore.GREEN}Connected successfully!{Fore.RESET}")

        print(f"\n{Fore.YELLOW}Fetching your channels and groups...{Fore.RESET}")
        channels = await fetch_channels(client)

        # Persist the visible list to .env (dialogs only — extras stay in EXTRA_CHANNELS)
        channels_data = [
            {
                "id": c["id"],
                "title": c["title"],
                "username": c["username"],
                "type": "channel" if c["is_channel"] else "group",
            }
            for c in channels
        ]
        save_channels_to_env(channels_data)

        print_channels(channels)

        print(f"\n{Fore.CYAN}What would you like to do?{Fore.RESET}")
        print("1. Exit (list only)")
        print("2. Download media from ONE channel/group")
        print("3. Download media from ALL channels/groups (dedup)")
        action = input(f"{Fore.CYAN}Choice (1-3): {Fore.RESET}").strip()

        if action == "1":
            return
        if action not in ("2", "3"):
            print(f"{Fore.RED}Invalid choice.{Fore.RESET}")
            return

        print(f"\n{Fore.CYAN}Choose content type:{Fore.RESET}")
        print("1. Videos only")
        print("2. Images only")
        print("3. PDFs only")
        print("4. ZIP files only")
        print("5. All media types")
        choice = input(f"{Fore.CYAN}Choice (1-5): {Fore.RESET}").strip()

        built = build_filter(choice)
        if not built:
            print(f"{Fore.RED}Invalid choice.{Fore.RESET}")
            return
        filter_type, folder_name, mime = built

        if action == "3":
            await run_all(client, channels, filter_type, folder_name, mime, state)
        else:
            await run_single(client, channels, filter_type, folder_name, mime, state)


if __name__ == "__main__":
    asyncio.run(main())