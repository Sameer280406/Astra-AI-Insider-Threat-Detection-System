from typing import Any, Iterable
import math


SEVERITY_RANK = {"medium": 0, "high": 1, "critical": 2}
POLICY_FEATURES = {
    "egress_mb",
    "sensitive_files",
    "off_hours",
    "new_device",
    "peer_deviation",
    "riskScore",
    "riskVelocity",
}


def evaluate_rules(rule_records: Iterable[Any], values: dict[str, float]) -> list[dict[str, Any]]:
    matches = []
    for record in rule_records:
        rule = record.data
        if not rule.get("enabled"):
            continue
        feature = rule["feature"]
        threshold = float(rule["threshold"])
        severity = rule.get("severity", "high")
        if feature not in POLICY_FEATURES:
            raise ValueError(f"Policy rule {record.id} has unsupported feature {feature!r}")
        if not math.isfinite(threshold):
            raise ValueError(f"Policy rule {record.id} contains a non-finite value")
        if severity not in SEVERITY_RANK:
            raise ValueError(f"Policy rule {record.id} has unsupported severity {severity!r}")
        if feature not in values:
            continue
        observed = float(values[feature])
        if not math.isfinite(observed):
            raise ValueError(f"Policy rule {record.id} contains a non-finite observed value")
        if observed >= threshold:
            matches.append({
                "id": record.id,
                "name": rule["name"],
                "feature": feature,
                "operator": ">=",
                "threshold": threshold,
                "observedValue": observed,
                "severity": severity,
            })
    return matches


def alert_severity(risk_score: float, matches: list[dict[str, Any]]) -> str:
    model_severity = "critical" if risk_score >= 85 else "high" if risk_score >= 65 else "medium"
    return max(
        [model_severity, *(rule["severity"] for rule in matches)],
        key=SEVERITY_RANK.__getitem__,
    )
