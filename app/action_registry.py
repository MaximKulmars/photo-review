from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EntityType = Literal["shelf", "album", "photo", "section", "selection", "quarantine"]
ActionStatus = Literal["ready", "planned", "disabled"]


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    icon: str
    entity: EntityType
    group: str
    order: int
    status: ActionStatus = "ready"
    destructive: bool = False
    disabled_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "icon": self.icon,
            "entity": self.entity,
            "group": self.group,
            "order": self.order,
            "status": self.status,
            "destructive": self.destructive,
            "disabled_reason": self.disabled_reason,
        }


REGISTRY: tuple[Action, ...] = (
    Action("shelf.createAlbum", "Создать альбом", "＋", "shelf", "main", 10),
    Action("shelf.importPhotos", "Импортировать фотографии", "⇪", "shelf", "main", 20, "planned"),
    Action("shelf.openUnsorted", "Просмотреть «Неразобранное»", "□", "shelf", "main", 30, "planned"),
    Action("shelf.startKiosk", "Запустить фотокиоск", "▶", "shelf", "kiosk", 40, "planned"),
    Action("album.addPhotos", "Добавить фотографии", "⇪", "album", "main", 10),
    Action("album.rename", "Переименовать", "✎", "album", "main", 20),
    Action("album.changeCover", "Изменить обложку", "▧", "album", "main", 30, "planned"),
    Action("album.editDescription", "Изменить описание", "☰", "album", "main", 40, "planned"),
    Action("album.editDate", "Изменить дату", "◷", "album", "main", 50, "planned"),
    Action("album.moveToShelf", "Переместить на другую полку", "⇄", "album", "main", 60, "planned"),
    Action("album.merge", "Объединить с альбомом", "⧉", "album", "main", 70, "planned"),
    Action("album.export", "Экспортировать альбом", "⇩", "album", "main", 80, "planned"),
    Action("album.share", "Поделиться", "↗", "album", "main", 90, "planned"),
    Action("album.startKiosk", "Запустить фотокиоск", "▶", "album", "kiosk", 100, "planned"),
    Action("album.quarantine", "Отправить в карантин", "!", "album", "danger", 110, "planned", True),
    Action("photo.addTags", "Добавить метки", "⌑", "photo", "main", 10, "planned"),
    Action("photo.addToSection", "Добавить в раздел", "▤", "photo", "main", 20, "planned"),
    Action("photo.setAsAlbumCover", "Сделать обложкой альбома", "▧", "photo", "main", 30, "planned"),
    Action("photo.editDateTime", "Изменить дату и время", "◷", "photo", "main", 40, "planned"),
    Action("photo.moveToAlbum", "Переместить в другой альбом", "⇄", "photo", "main", 50, "planned"),
    Action("photo.rotate", "Повернуть", "↻", "photo", "main", 60, "planned"),
    Action("photo.downloadOriginal", "Скачать оригинал", "⇩", "photo", "main", 70, "planned"),
    Action("photo.showInfo", "Показать информацию", "i", "photo", "info", 80),
    Action("photo.quarantine", "Отправить в карантин", "!", "photo", "danger", 90, destructive=True),
    Action("section.addSelectedPhotos", "Добавить выбранные фотографии", "＋", "section", "main", 10, "planned"),
    Action("section.rename", "Переименовать", "✎", "section", "main", 20, "planned"),
    Action("section.reorder", "Изменить порядок", "⇅", "section", "main", 30, "planned"),
    Action("section.delete", "Удалить раздел", "!", "section", "danger", 40, "planned", True),
    Action("selection.addTags", "Добавить метки", "⌑", "selection", "main", 10, "planned"),
    Action("selection.addToSection", "Добавить в раздел", "▤", "selection", "main", 20, "planned"),
    Action("selection.move", "Переместить", "⇄", "selection", "main", 30, "planned"),
    Action("selection.export", "Экспортировать", "⇩", "selection", "main", 40, "planned"),
    Action("selection.quarantine", "Отправить в карантин", "!", "selection", "danger", 50, destructive=True),
    Action("quarantine.restore", "Восстановить", "↩", "quarantine", "main", 10),
    Action("quarantine.showOrigin", "Показать исходное расположение", "⌖", "quarantine", "main", 20),
    Action("quarantine.deleteForever", "Удалить окончательно", "!", "quarantine", "danger", 30, destructive=True),
)


def actions_for(entity: EntityType, *, empty_album: bool = False) -> list[dict[str, object]]:
    actions = [action.as_dict() for action in REGISTRY if action.entity == entity]
    if entity == "album" and empty_album:
        for action in actions:
            if action["id"] == "album.changeCover":
                action["status"] = "disabled"
                action["disabled_reason"] = "В пустом альбоме пока нет фотографий для обложки."
    return sorted(actions, key=lambda action: int(action["order"]))
