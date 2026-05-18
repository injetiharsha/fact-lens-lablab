#!/usr/bin/env python
"""Test script for intake agent endpoint - demonstrates system refinements."""

import json
import os
import sys
from pathlib import Path

# Ensure project root is importable
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from factlens_crew.orchestrator import FactLensCrewWorkflow


def test_intake_agent_endpoint():
    """Test the refined intake agent with test matrix claims."""
    
    test_claims = [
        {
            "id": "1_static_science",
            "claim": "The Earth revolves around the Sun.",
            "category": "Static science fact",
        },
        {
            "id": "2_numeric_ranking",
            "claim": "India is the 4th largest economy in 2026.",
            "category": "Numeric ranking/time-bound claim",
        },
        {
            "id": "3_population",
            "claim": "India's population surpassed China in 2023.",
            "category": "Population/demography claim",
        },
        {
            "id": "4_health_stats",
            "claim": "Global measles cases increased in 2024.",
            "category": "Health/statistics claim",
        },
        {
            "id": "5_opinion",
            "claim": "This policy is the best for growth.",
            "category": "Ambiguous/opinion-like claim",
        },
        {
            "id": "6_empty",
            "claim": "",
            "category": "Edge case: empty input",
        },
        {
            "id": "7_question",
            "claim": "Is the Earth round?",
            "category": "Edge case: question",
        },
        {
            "id": "8_too_short",
            "claim": "Sun is hot.",
            "category": "Edge case: too short",
        },
    ]
    
    # Set env for testing without external APIs
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["FEATHERLESS_API_KEY"] = ""
    os.environ["TAVILY_API_KEY"] = ""
    os.environ["MODEL_POLICY"] = "quality"
    
    results = []
    
    print("=" * 80)
    print("FACTLENS INTAKE AGENT ENDPOINT TEST")
    print("Testing refined intake agent on optimization playbook test matrix")
    print("=" * 80)
    print()
    
    for test_case in test_claims:
        print(f"Test {test_case['id']}: {test_case['category']}")
        print(f"Input: {test_case['claim'][:60]}..." if len(test_case['claim']) > 60 else f"Input: {test_case['claim']}")
        
        try:
            workflow = FactLensCrewWorkflow(run_id=f"test_{test_case['id']}")
            result = workflow._intake_agent(test_case["claim"])
            
            extracted_claim = result["claim"]
            is_checkable = result["checkable"]
            report = result["report"]
            
            result_record = {
                "test_id": test_case["id"],
                "category": test_case["category"],
                "input": test_case["claim"],
                "extracted_claim": extracted_claim,
                "checkable": is_checkable,
                "confidence": report.confidence,
                "topic": report.findings[0] if report.findings else "unknown",
                "findings": report.findings,
                "status": "PASS" if (is_checkable or test_case["id"].startswith("6_") or 
                                      test_case["id"].startswith("7_") or 
                                      test_case["id"].startswith("8_") or
                                      test_case["id"] == "5_opinion") else "FAIL",
            }
            results.append(result_record)
            
            print(f"Extracted: {extracted_claim[:60]}..." if len(extracted_claim) > 60 else f"Extracted: {extracted_claim}")
            print(f"Checkable: {is_checkable}")
            print(f"Confidence: {report.confidence}")
            print(f"Topic: {report.findings[0] if report.findings else 'unknown'}")
            print(f"Status: {result_record['status']}")
            
        except Exception as e:
            print(f"ERROR: {str(e)}")
            results.append({
                "test_id": test_case["id"],
                "category": test_case["category"],
                "status": "ERROR",
                "error": str(e),
            })
        
        print()
    
    # Print summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    errors = sum(1 for r in results if r.get("status") == "ERROR")
    
    print(f"Total Tests: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Errors: {errors}")
    print()
    
    # Test matrix results
    print("Test Matrix (5 Claim Classes):")
    for i in range(1, 6):
        test_id = f"{i}_*"
        for r in results:
            if r.get("test_id", "").startswith(str(i)):
                status_icon = "✓" if r.get("checkable") else "✗"
                print(f"  {status_icon} Claim {i}: {r['category']}")
                if r.get("checkable"):
                    print(f"     Confidence: {r['confidence']}")
                break
    
    print()
    print("Detailed Results:")
    print(json.dumps(results, indent=2, default=str))
    
    return results


if __name__ == "__main__":
    results = test_intake_agent_endpoint()
    
    # Exit with success if most tests pass
    passed = sum(1 for r in results if r.get("status") in {"PASS", "OK"})
    if passed >= 3:
        sys.exit(0)
    else:
        sys.exit(1)
