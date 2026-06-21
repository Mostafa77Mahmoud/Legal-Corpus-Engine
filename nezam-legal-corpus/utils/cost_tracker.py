from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CallRecord:
    stage: str
    law_id: str
    model: str
    input_tokens: int
    output_tokens: int
    image_pages: int
    input_cost_usd: float
    output_cost_usd: float
    image_cost_usd: float
    total_cost_usd: float
    timestamp: str


@dataclass
class CostTracker:
    _records: list[CallRecord] = field(default_factory=list)

    def record(
        self,
        stage: str,
        law_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        image_pages: int = 0,
        input_cost_per_1m: float = 0.10,
        output_cost_per_1m: float = 0.40,
        image_cost_per_page: float = 0.00258,
    ) -> CallRecord:
        input_cost = (input_tokens / 1_000_000) * input_cost_per_1m
        output_cost = (output_tokens / 1_000_000) * output_cost_per_1m
        image_cost = image_pages * image_cost_per_page
        total_cost = input_cost + output_cost + image_cost

        record = CallRecord(
            stage=stage,
            law_id=law_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_pages=image_pages,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            image_cost_usd=image_cost,
            total_cost_usd=total_cost,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
        self._records.append(record)
        return record

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self._records)

    @property
    def total_image_pages(self) -> int:
        return sum(r.image_pages for r in self._records)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.total_cost_usd for r in self._records)

    @property
    def records(self) -> list[CallRecord]:
        return list(self._records)

    def summary(self) -> dict:
        by_stage: dict[str, dict] = {}
        for r in self._records:
            if r.stage not in by_stage:
                by_stage[r.stage] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "image_pages": 0,
                    "cost_usd": 0.0,
                }
            by_stage[r.stage]["calls"] += 1
            by_stage[r.stage]["input_tokens"] += r.input_tokens
            by_stage[r.stage]["output_tokens"] += r.output_tokens
            by_stage[r.stage]["image_pages"] += r.image_pages
            by_stage[r.stage]["cost_usd"] += r.total_cost_usd

        return {
            "total_api_calls": len(self._records),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_image_pages": self.total_image_pages,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "by_stage": by_stage,
        }
