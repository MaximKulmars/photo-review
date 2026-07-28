from app.action_registry import actions_for


def ids(entity, **context):
    return [action["id"] for action in actions_for(entity, **context)]


def test_album_menu_order_and_empty_cover_state():
    assert ids("album") == [
        "album.addPhotos",
        "album.rename",
        "album.changeCover",
        "album.editDescription",
        "album.editDate",
        "album.moveToShelf",
        "album.merge",
        "album.export",
        "album.share",
        "album.startKiosk",
        "album.quarantine",
    ]

    change_cover = next(action for action in actions_for("album", empty_album=True) if action["id"] == "album.changeCover")
    assert change_cover["status"] == "disabled"
    assert "пустом альбоме" in change_cover["disabled_reason"]


def test_photo_and_quarantine_menus_are_state_specific():
    assert ids("photo") == [
        "photo.addTags",
        "photo.addToSection",
        "photo.setAsAlbumCover",
        "photo.editDateTime",
        "photo.moveToAlbum",
        "photo.rotate",
        "photo.downloadOriginal",
        "photo.showInfo",
        "photo.quarantine",
    ]
    assert ids("quarantine") == [
        "quarantine.restore",
        "quarantine.showOrigin",
        "quarantine.deleteForever",
    ]


def test_shelf_import_is_not_a_direct_shelf_upload_action():
    shelf_ids = ids("shelf")
    assert shelf_ids == [
        "shelf.createAlbum",
        "shelf.importPhotos",
        "shelf.openUnsorted",
        "shelf.startKiosk",
    ]
    assert "shelf.addPhotos" not in shelf_ids
