"""Parse the raw export CSVs into typed records (never passes raw blobs downstream)."""

import csv
import io
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from .scanner import HEADER_HINTS, _detect_header_row, _parse_rows


def read_rows(path: str) -> list:
    """Read a CSV into dict rows, skipping LinkedIn's Notes preamble."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    rows = _parse_rows(text)
    hint = HEADER_HINTS.get(os.path.basename(path))
    idx = _detect_header_row(rows, hint)
    if idx >= len(rows):
        return []
    header = [h.strip().lstrip('\ufeff') for h in rows[idx]]
    out = []
    for r in rows[idx + 1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({header[i]: r[i].strip() for i in range(min(len(header), len(r)))}
                   if len(r) >= len(header)
                   else {header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))})
    return out


@dataclass
class ConnectionRecord:
    first_name: str = ""
    last_name: str = ""
    url: str = ""
    email: str = ""
    company: str = ""
    position: str = ""
    connected_on: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def to_dict(self):
        return asdict(self)


@dataclass
class MessageRecord:
    conversation_id: str = ""
    title: str = ""
    from_name: str = ""
    from_url: str = ""
    to_name: str = ""
    to_url: str = ""
    date: str = ""
    subject: str = ""
    content: str = ""
    folder: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class InvitationRecord:
    from_name: str = ""
    to_name: str = ""
    sent_at: str = ""
    message: str = ""
    direction: str = ""
    inviter_url: str = ""
    invitee_url: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class EducationRecord:
    school: str = ""
    start_date: str = ""
    end_date: str = ""
    degree: str = ""
    activities: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class CompanyFollowRecord:
    organization: str = ""
    followed_on: str = ""

    def to_dict(self):
        return asdict(self)


def parse_connections(path: str) -> list:
    out = []
    for r in read_rows(path):
        out.append(ConnectionRecord(
            first_name=r.get("First Name", ""),
            last_name=r.get("Last Name", ""),
            url=r.get("URL", ""),
            email=r.get("Email Address", ""),
            company=r.get("Company", ""),
            position=r.get("Position", ""),
            connected_on=r.get("Connected On", ""),
        ))
    return out


_MESSAGE_FILES = ["messages.csv", "guide_messages.csv",
                  "learning_coach_messages.csv", "learning_role_play_messages.csv"]


def parse_messages(source_dir: str) -> list:
    out = []
    for fname in _MESSAGE_FILES:
        path = os.path.join(source_dir, fname)
        if not os.path.isfile(path):
            continue
        for r in read_rows(path):
            out.append(MessageRecord(
                conversation_id=r.get("CONVERSATION ID", ""),
                title=r.get("CONVERSATION TITLE", ""),
                from_name=r.get("FROM", ""),
                from_url=r.get("SENDER PROFILE URL", ""),
                to_name=r.get("TO", ""),
                to_url=r.get("RECIPIENT PROFILE URLS", ""),
                date=r.get("DATE", ""),
                subject=r.get("SUBJECT", ""),
                content=r.get("CONTENT", ""),
                folder=r.get("FOLDER", ""),
            ))
    return out


def parse_invitations(path: str) -> list:
    out = []
    for r in read_rows(path):
        out.append(InvitationRecord(
            from_name=r.get("From", ""),
            to_name=r.get("To", ""),
            sent_at=r.get("Sent At", ""),
            message=r.get("Message", ""),
            direction=r.get("Direction", ""),
            inviter_url=r.get("inviterProfileUrl", ""),
            invitee_url=r.get("inviteeProfileUrl", ""),
        ))
    return out


def parse_education(path: str) -> list:
    out = []
    for r in read_rows(path):
        out.append(EducationRecord(
            school=r.get("School Name", ""),
            start_date=r.get("Start Date", ""),
            end_date=r.get("End Date", ""),
            degree=r.get("Degree Name", ""),
            activities=r.get("Activities", ""),
        ))
    return out


def parse_company_follows(path: str) -> list:
    out = []
    for r in read_rows(path):
        out.append(CompanyFollowRecord(
            organization=r.get("Organization", ""),
            followed_on=r.get("Followed On", ""),
        ))
    return out


def parse_profile(path: str) -> list:
    return read_rows(path)
