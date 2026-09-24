def register_all(app):
    from . import main
    from . import watch
    from . import settings
    from . import playlists
    from . import artists
    from . import duplicates
    from . import editor

    main.register(app)
    watch.register(app)
    settings.register(app)
    playlists.register(app)
    artists.register(app)
    duplicates.register(app)
    editor.register(app)