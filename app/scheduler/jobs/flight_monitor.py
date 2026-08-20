import logging
from datetime import datetime

from sqlalchemy import select

from app.database import get_db_session
from app.models.trips import FlightWatch, PriceSnapshot
from app.services import claude_service, duffel_service, sms_service
from app.config import settings

logger = logging.getLogger(__name__)


async def monitor_flight_prices() -> None:
    logger.info("Flight monitor: starting price check run")
    async with get_db_session() as db:
        result = await db.execute(select(FlightWatch).where(FlightWatch.is_active == True))
        watches = result.scalars().all()
        logger.info(f"Flight monitor: checking {len(watches)} active watches")

        for watch in watches:
            try:
                offers = await duffel_service.search_flights(
                    origin=watch.origin,
                    destination=watch.destination,
                    depart_date=watch.depart_date.isoformat(),
                    return_date=watch.return_date.isoformat() if watch.return_date else None,
                    adults=watch.num_adults,
                    cabin=watch.cabin_class,
                    max_results=5,
                )
                if not offers:
                    logger.info(f"No offers for watch {watch.id} ({watch.origin}->{watch.destination})")
                    continue

                lowest = min(offers, key=lambda x: x["price"])
                snapshot = PriceSnapshot(
                    flight_watch_id=watch.id,
                    lowest_price=lowest["price"],
                    currency=lowest["currency"],
                    raw_response={"offers": offers},
                )
                db.add(snapshot)

                should_alert = False
                if watch.target_price and lowest["price"] <= float(watch.target_price):
                    should_alert = True
                else:
                    # Check for 10%+ drop from last snapshot
                    prev_result = await db.execute(
                        select(PriceSnapshot)
                        .where(PriceSnapshot.flight_watch_id == watch.id)
                        .order_by(PriceSnapshot.checked_at.desc())
                        .limit(2)
                    )
                    prev_snaps = prev_result.scalars().all()
                    if len(prev_snaps) >= 2:
                        prev_price = float(prev_snaps[1].lowest_price or 0)
                        if prev_price > 0:
                            drop_pct = (prev_price - lowest["price"]) / prev_price
                            if drop_pct >= 0.10:
                                should_alert = True

                if should_alert and settings.cordia_phone_number:
                    route = f"{watch.origin} → {watch.destination}"
                    msg = (
                        f"Flight alert: {route} dropped to ${lowest['price']:.0f} "
                        f"({lowest['carrier']}, {watch.cabin_class}) for {watch.depart_date}. "
                        f"Reply 'details' for full options."
                    )
                    if await sms_service.send_sms(to=settings.cordia_phone_number, body=msg):
                        snapshot.alerted = True
                        # So "book it" / "details" replies land with context.
                        await claude_service.record_assistant_message(
                            db, settings.cordia_phone_number, msg
                        )

                watch.last_checked_at = datetime.utcnow()
                await db.commit()

            except Exception as e:
                logger.error(f"Flight monitor error for watch {watch.id}: {e}")
