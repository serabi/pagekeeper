"""Reading statistics aggregation for the reading log and stats tab."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.database_service import DatabaseService

READING_STATUSES = {'active', 'completed', 'paused', 'dnf', 'not_started'}
MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


@dataclass
class ReadingStatsService:
    """Compute local reading stats from books, states, and yearly goals."""

    database_service: DatabaseService

    def get_year_stats(self, year: int) -> dict:
        books = [
            book for book in self.database_service.get_all_books()
            if getattr(book, 'status', None) in READING_STATUSES
        ]
        states_by_book = {}
        for state in self.database_service.get_all_states():
            states_by_book.setdefault(state.abs_id, []).append(state)

        monthly_finished = [0] * 12
        books_finished = 0
        currently_reading = 0
        ratings = []

        for book in books:
            progress = self._max_progress_percent(states_by_book.get(book.abs_id, []))
            if self._is_genuinely_reading(book, progress):
                currently_reading += 1

            if book.status == 'completed' and self._year_of(getattr(book, 'finished_at', None)) == year:
                books_finished += 1
                month_idx = self._month_index(book.finished_at)
                if month_idx is not None:
                    monthly_finished[month_idx] += 1
                rating = getattr(book, 'rating', None)
                if rating is not None:
                    ratings.append(float(rating))

        goal = self.database_service.get_reading_goal(year)
        goal_target = goal.target_books if goal else None
        goal_percent = 0.0
        if goal_target and goal_target > 0:
            goal_percent = min(round((books_finished / goal_target) * 100, 1), 100.0)

        average_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

        return {
            'year': year,
            'books_finished': books_finished,
            'monthly_finished': monthly_finished,
            'monthly_labels': MONTH_LABELS,
            'currently_reading': currently_reading,
            'total_tracked': len(books),
            'average_rating': average_rating,
            'goal_target': goal_target,
            'goal_completed': books_finished,
            'goal_percent': goal_percent,
        }

    @staticmethod
    def _max_progress_percent(states) -> float:
        max_progress = 0.0
        for state in states:
            pct = getattr(state, 'percentage', None)
            if pct:
                max_progress = max(max_progress, float(pct) * 100.0)
        return min(max_progress, 100.0)

    @staticmethod
    def _is_genuinely_reading(book, progress_percent: float) -> bool:
        status = getattr(book, 'status', None)
        if status != 'active':
            return False
        return progress_percent > 1.0

    @staticmethod
    def _year_of(date_str: str | None) -> int | None:
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str).year
        except ValueError:
            return None

    @staticmethod
    def _month_index(date_str: str | None) -> int | None:
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str).month - 1
        except ValueError:
            return None
