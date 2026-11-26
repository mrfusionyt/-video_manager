def register_all(app):
    # Auth must install its before_request hook before any route is served.
    from . import auth
    from . import main
    from . import watch
    from . import settings
    from . import playlists
    from . import artists
    from . import duplicates
    from . import editor
    from . import tasks

    auth.register(app)
    main.register(app)
    watch.register(app)
    settings.register(app)
    playlists.register(app)
    artists.register(app)
    duplicates.register(app)
    editor.register(app)
    tasks.register(app)