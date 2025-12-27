from syncronus.sources import SpotifyClient, TidalClient
from dotenv import load_dotenv
import rich
from syncronus.logger import get_logger

load_dotenv()

logger = get_logger(__name__)
# client = SpotifyClient()
# url = client.authenticate()
# if url:
#     print(f"Please visit this URL to authorize the app: {url}")
#     client.exchange_code(input("Paste the ?code=: "))
# else:
#     print("Already authenticated.")
#     playlists = client.get_all_playlists()
#     for playlist in playlists:
#         rich.print(playlist.to_dict())
#         rich.print("-" * 20)

# TIDAL Client
client = TidalClient()
url = client.authenticate()
if url:
    print(f"Please visit this URL to authorize the app: {url}")
    client.exchange_code(input("Paste the ?code=: "))

print("Already authenticated.")
playlists = client.get_all_playlists()
rich.print(playlists)
# playlists = client.get_all_playlists()
# for playlist in playlists:
#     rich.print(playlist.to_dict())
#     rich.print("-" * 20)
