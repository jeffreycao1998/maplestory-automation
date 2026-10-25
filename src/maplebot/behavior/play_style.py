"""Optional, repeatable timing variation for ordinary engagement only."""
import random


class PlayTiming:
    def __init__(self, settings):
        self.settings = settings
        self.rng = random.Random(settings.play_style.seed)
        self.seen_at = None
        self.ready_at = 0.0
        self.cast_extra = 0.0

    def ready(self, now):
        style = self.settings.play_style
        if not style.enabled:
            return True
        if self.seen_at is None or now-self.seen_at > .6:
            self.ready_at = now+self.rng.uniform(style.reaction_min_s, style.reaction_max_s)
        self.seen_at = now
        return now >= self.ready_at

    def note_attack(self):
        style = self.settings.play_style
        self.cast_extra = self.rng.uniform(0, style.cast_extra_max_s) if style.enabled else 0.0
