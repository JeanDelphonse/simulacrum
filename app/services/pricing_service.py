"""FR-DISC-10: Single source of truth for simulation pricing."""
from flask import current_app


def get_current_price() -> dict:
    """Return a pricing dict reflecting any active discount.

    Keys always present:
      base_price_cents       int   — base price from platform_settings
      discounted_price_cents int   — price to charge (== base if no discount)
      discount_percentage    int   — 0 if no discount
      is_discounted          bool
      label                  str|None
      expires_at             str|None  — ISO 8601 UTC, None if no discount
    """
    from app.models.platform_settings import PlatformSetting
    from app.models.discount import SimulationDiscount

    try:
        base = int(
            PlatformSetting.get('simulation_price')
            or current_app.config['SIMULATION_PRICE_CENTS']
        )
    except Exception:
        base = int(current_app.config.get('SIMULATION_PRICE_CENTS', 69500))

    discount = SimulationDiscount.get_active()
    if discount:
        discounted = int(base * (1 - discount.discount_percentage / 100))
        return {
            'base_price_cents': base,
            'discounted_price_cents': discounted,
            'discount_percentage': discount.discount_percentage,
            'is_discounted': True,
            'label': discount.label,
            'expires_at': discount.end_at.isoformat(),
        }
    return {
        'base_price_cents': base,
        'discounted_price_cents': base,
        'discount_percentage': 0,
        'is_discounted': False,
        'label': None,
        'expires_at': None,
    }


def price_for_user(user_id) -> dict:
    """What this specific buyer pays, honouring any admin comp granted to them.

    The same dict `get_current_price()` returns, plus:

      user_discount_id         str|None  — the grant that applied, if one did
      user_discount_percentage int       — 0 when no grant applied
      is_comped                bool      — a per-user grant set the price

    The global sale and a per-user comp are independent and never stack: a
    buyer gets whichever is the better deal, because stacking percentages is
    both ambiguous (is 50% + 50% free, or 75% off?) and a way to hand out a
    100% discount by accident.

    This does not consume the grant — pricing is a read, and a user may see
    their price several times before they buy. `redeem_user_discount()` is what
    spends it, once, at the moment the simulation is actually paid for.
    """
    pricing = get_current_price()
    pricing.update({
        'user_discount_id': None,
        'user_discount_percentage': 0,
        'is_comped': False,
    })
    if not user_id:
        return pricing

    try:
        from app.models.discount import UserDiscount
        grant = UserDiscount.live_for_user(user_id)
    except Exception:
        # A missing table (migration not run) or any lookup failure must never
        # stop someone buying. Failing open here means they pay the normal
        # price, which is recoverable; failing closed would block checkout.
        return pricing

    if grant is None:
        return pricing

    pricing['user_discount_id'] = grant.id
    pricing['user_discount_percentage'] = int(grant.discount_percentage)

    # Better deal wins. On a tie the global sale is left in place, so the
    # buyer's comp stays unspent for a purchase where it actually helps.
    if int(grant.discount_percentage) <= pricing['discount_percentage']:
        return pricing

    base = pricing['base_price_cents']
    pricing['discounted_price_cents'] = int(base * (1 - grant.discount_percentage / 100))
    pricing['discount_percentage'] = int(grant.discount_percentage)
    pricing['is_discounted'] = True
    pricing['is_comped'] = True
    pricing['label'] = grant.reason or 'Complimentary'
    pricing['expires_at'] = grant.expires_at.isoformat() if grant.expires_at else None
    return pricing


def redeem_user_discount(user_id: str, sim, pricing: dict) -> bool:
    """Spend one use of the grant that priced this simulation.

    Mirrors `org_service.try_redeem_credit`: a guarded UPDATE rather than a
    read-then-write, so two concurrent checkouts cannot both spend the last use
    of a one-use comp. Adds the redemption row to the session and leaves the
    commit to the caller, which is already committing the simulation.

    Returns True when a use was spent. False means the grant was consumed or
    revoked between pricing and checkout — the caller should re-price rather
    than assume the discount still holds.
    """
    if not pricing.get('is_comped') or not pricing.get('user_discount_id'):
        return False

    from app.extensions import db
    from app.models.discount import UserDiscount, UserDiscountRedemption

    discount_id = pricing['user_discount_id']
    # A savepoint, not a bare try/except: the caller is mid-transaction with an
    # unsaved simulation, so a failure in here has to roll back the redemption
    # *only*. Swallowing the exception without one would leave the session dirty
    # and take the caller's commit down with it.
    try:
        nested = db.session.begin_nested()
        try:
            query = db.session.query(UserDiscount).filter(
                UserDiscount.id == discount_id,
                UserDiscount.revoked_at.is_(None),
            )
            grant = query.first()
            if grant is None:
                nested.rollback()
                return False

            if grant.is_unlimited:
                # Nothing to guard against: an unlimited grant cannot be
                # oversold. The counter is still bumped, because "how often was
                # this used" is the number that tells an admin a standing comp
                # needs revisiting.
                updated = query.update(
                    {UserDiscount.uses_count: UserDiscount.uses_count + 1},
                    synchronize_session=False,
                )
            else:
                updated = query.filter(
                    UserDiscount.uses_count < UserDiscount.max_uses,
                ).update(
                    {UserDiscount.uses_count: UserDiscount.uses_count + 1},
                    synchronize_session=False,
                )
            if not updated:
                nested.rollback()
                return False

            db.session.add(UserDiscountRedemption(
                discount_id=discount_id,
                user_id=user_id,
                simulation_id=getattr(sim, 'id', None),
                base_price_cents=int(pricing.get('base_price_cents') or 0),
                charged_cents=int(pricing.get('discounted_price_cents') or 0),
            ))
            nested.commit()
            return True
        except Exception:
            nested.rollback()
            raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            'redeem_user_discount failed for user %s grant %s: %s',
            user_id, discount_id, exc,
        )
        return False


def get_prospect_tier_config() -> dict:
    """Return the admin-configurable prospect tier settings.

    Keys: tier1_count, tier2_count, tier2_price_cents, tier3_count, tier3_price_cents.
    All values are read from PlatformSetting with safe integer defaults.
    """
    from app.models.platform_settings import PlatformSetting

    def _int(key, default):
        try:
            return int(PlatformSetting.get(key) or default)
        except Exception:
            return default

    return {
        'tier1_count':        _int('prospect_tier1_count', 5),
        'tier2_count':        _int('prospect_tier2_count', 10),
        'tier2_price_cents':  _int('prospect_tier2_price_cents', 500),
        'tier3_count':        _int('prospect_tier3_count', 15),
        'tier3_price_cents':  _int('prospect_tier3_price_cents', 1000),
    }


def format_price_usd(cents: int) -> str:
    """Format cents as a USD string, e.g. 1595 → '$15.95', 69500 → '$695'."""
    return f'${cents // 100:,}' if cents % 100 == 0 else f'${cents / 100:,.2f}'
