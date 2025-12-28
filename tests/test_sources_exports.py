def test_sources_exports():
    from syncronus.sources import (
        BaseClient,
        Song,
        Playlist,
        SpotifyClient,
        TidalClient,
    )

    assert BaseClient
    assert Song
    assert Playlist
    assert SpotifyClient
    assert TidalClient
