from datetime import date

from pydantic import BaseModel


class DateRangeReport(BaseModel):
    start_date: date
    end_date: date
    total_deliveries: int
    completed: int
    pending: int
    cancelled: int
    total_full_delivered: int
    total_empty_collected: int


class CustomerDeliveryReport(BaseModel):
    customer_id: int
    customer_name: str
    total_deliveries: int
    completed: int
    pending: int
    total_full_delivered: int
    total_empty_collected: int
