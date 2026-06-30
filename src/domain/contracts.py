from typing import Literal, TypeAlias, TypedDict, TypeGuard

from ..bangumi_types import JsonValue


class SearchSubjectItem(TypedDict, total=False):
    id: int | str
    name: str
    name_cn: str
    type: int


class SearchSubjectsResponse(TypedDict):
    data: list[SearchSubjectItem]


class EpisodeItem(TypedDict, total=False):
    id: int
    subject_id: int
    type: int
    ep: int
    sort: int
    name: str
    name_cn: str
    airdate: str
    comment: int
    disc: int
    duration: str
    duration_seconds: int


class EpisodeListResponse(TypedDict):
    data: list[EpisodeItem]


class SubjectDetailsResponse(TypedDict, total=False):
    id: int | str
    name: str
    name_cn: str
    date: str
    air_date: str
    eps: int
    episodes: list[EpisodeItem]
    platform: str
    type: int
    images: dict[str, JsonValue]
    image_url: str
    summary: str
    tags: list[dict[str, JsonValue]]
    infobox: list[dict[str, JsonValue]]
    total_episodes: int
    rating: dict[str, JsonValue]
    episode_list: list[dict[str, JsonValue]]
    air_weekday: str


class CalendarWeekday(TypedDict, total=False):
    id: int
    cn: str
    en: str
    ja: str


class CalendarItem(TypedDict, total=False):
    id: int | str
    name: str
    name_cn: str
    images: dict[str, JsonValue]


class CalendarDay(TypedDict, total=False):
    weekday: CalendarWeekday
    items: list[CalendarItem]
    is_today: bool


class UserDetailsResponse(TypedDict, total=False):
    id: int | str
    username: str
    nickname: str


class PersonDetailsResponse(TypedDict, total=False):
    id: int | str
    name: str
    summary: str


class PersonsSearchResponse(TypedDict):
    data: list[PersonDetailsResponse]


class SubscribeMatch(TypedDict):
    subject_id: str
    name: str
    air_date: str
    total_episodes: int


class SubscribeCandidate(TypedDict):
    subject_id: str
    name: str


class UnsubscribeMatch(TypedDict):
    subject_id: str
    name: str


EpisodeCardVariant: TypeAlias = Literal[
    "pastel_lightbox",
    "editorial_digest",
    "cinematic_poster",
]
EPISODE_CARD_VARIANTS: tuple[EpisodeCardVariant, ...] = (
    "pastel_lightbox",
    "editorial_digest",
    "cinematic_poster",
)
DEFAULT_EPISODE_CARD_VARIANT: EpisodeCardVariant = "pastel_lightbox"
RenderData: TypeAlias = dict[str, JsonValue]
MessageResult: TypeAlias = object


def is_episode_card_variant(value: object) -> TypeGuard[EpisodeCardVariant]:
    return isinstance(value, str) and value in EPISODE_CARD_VARIANTS
