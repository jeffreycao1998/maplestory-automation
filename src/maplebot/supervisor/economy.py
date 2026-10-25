"""Observed wallet movement and explicitly labelled stock-replacement estimates."""
from __future__ import annotations


class EconomyTracker:
    def __init__(self, settings):
        self.settings = settings
        self.started = None
        self.wallet_start = None
        self.wallet_last = None
        self.last_stock = {"hp":None,"mp":None}
        self.decreases = {"hp":0,"mp":0}
        self.stock_seen = {"hp":False,"mp":False}
        self.pickup_attempts = 0
        self.wallet_fresh = False
        self.stock_fresh = {"hp":False,"mp":False}

    def observe(self, p, now):
        if self.started is None:
            self.started = now
        # OCR values are absent when stale/unconfirmed; don't erase an established baseline.
        self.wallet_fresh = p.mesos is not None
        if p.mesos is not None:
            if self.wallet_start is None:
                self.wallet_start = p.mesos
            self.wallet_last = p.mesos
        for kind in ("hp","mp"):
            count = getattr(p,kind+"_stock")
            self.stock_fresh[kind] = count is not None
            if count is None:
                continue
            previous = self.last_stock[kind]
            if previous is not None and count < previous:
                self.decreases[kind] += previous-count
            self.last_stock[kind] = count
            self.stock_seen[kind] = True

    def summary(self, now):
        delta = None if self.wallet_start is None else self.wallet_last-self.wallet_start
        cost = 0
        known = True
        for kind in ("hp","mp"):
            price = getattr(self.settings.economy,kind+"_replacement_cost")
            # If that potion is used by this setup, unknown stock means unknown cost.
            if getattr(self.settings.keys,kind+"_potion") and not self.stock_fresh[kind]:
                known = False
            if self.decreases[kind] and price is None:
                known = False
            elif price is not None:
                cost += self.decreases[kind]*price
        cost = cost if known else None
        visible_delta = delta if self.wallet_fresh else None
        net = visible_delta-cost if visible_delta is not None and cost is not None else None
        duration = max(0,now-(self.started if self.started is not None else now))
        return {"wallet_delta":visible_delta,"wallet_last_known_delta":delta,"stock_decrease":dict(self.decreases),
                "replacement_cost_estimate":cost,"net_estimate":net,
                "net_per_hour":net*3600/duration if net is not None and duration>0 else None,
                "pickup_attempts":self.pickup_attempts,
                "basis":"wallet change minus valued visible potion-stock decreases; not proof of kills, purchases, pickup success, or realized profit"}
