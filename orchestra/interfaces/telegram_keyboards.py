"""Telegram inline keyboard builders enforcing <=64-byte UTF-8 callback_data (§10, TG-16)."""

from __future__ import annotations

from dataclasses import dataclass

from orchestra.config.constants import TG_CALLBACK_DATA_LIMIT


@dataclass(frozen=True, slots=True)
class InlineButtonSpec:
    """Transport-neutral inline button validated for Telegram's 64-byte callback limit."""

    text: str
    callback_data: str

    def __post_init__(self) -> None:
        """Fail fast if `callback_data` exceeds 64 UTF-8 bytes (`TG-16`)."""
        raw_len = len(self.callback_data.encode("utf-8"))
        if raw_len == 0 or raw_len > TG_CALLBACK_DATA_LIMIT:
            raise ValueError(
                f"Telegram callback_data must be 1..{TG_CALLBACK_DATA_LIMIT} UTF-8 bytes "
                f"(got {raw_len} bytes for '{self.callback_data}')"
            )


def build_sitrep_keyboard(
    question_id: str,
    options_ru: tuple[str, ...] | list[str],
) -> list[list[InlineButtonSpec]]:
    """Build 1-per-row numbered inline option buttons for a Russian SITREP escalation."""
    short_qid = question_id[-24:]
    rows: list[list[InlineButtonSpec]] = []
    for idx, label in enumerate(options_ru, start=1):
        cb = f"ans:{short_qid}:{idx}"
        display = f"{idx}. {label}"[:48]
        rows.append([InlineButtonSpec(text=display, callback_data=cb)])
    return rows


def build_team_approval_keyboard(project_id: str) -> list[list[InlineButtonSpec]]:
    """Build S3 Org Design approval buttons ('Запустить' / 'Изменить стек')."""
    short_pid = project_id[:24]
    return [
        [
            InlineButtonSpec(text="Запустить", callback_data=f"s3:go:{short_pid}"),
            InlineButtonSpec(text="Изменить стек", callback_data=f"s3:edit:{short_pid}"),
        ]
    ]
