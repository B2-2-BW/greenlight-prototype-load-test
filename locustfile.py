# locustfile.py
import os
import json
import time
import requests
from locust import FastHttpUser, task, events, constant

ACTION_ID = 13
DESTINATION_URL = "/items/test-item-123"
HOST = "http://greenlight-core-api-alb-104707260.ap-northeast-2.elb.amazonaws.com"
SSE_TIMEOUT_SECONDS = float(os.getenv("SSE_TIMEOUT_SECONDS", "60"))
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))

class QueueUser(FastHttpUser):
    wait_time = constant(0)
    
    @task
    def queue_flow(self):
        """
        Scenario per user:
        1) POST /api/v1/queue/check-or-enter
        2) Save customerId, jwtToken
        3) Connect SSE /waiting/sse?actionId=...&customerId=...
        4) When waitStatus == READY -> close
        5) POST /api/v1/customer/verify with header token: jwtToken
        6) Count as one cycle
        7) Retry up to 3 times
        """
        # Step 1: check-or-enter
        with self.client.post(
            "/api/v1/queue/check-or-enter",
            json={"actionId": ACTION_ID, "destinationUrl": DESTINATION_URL},
            name="/check_or_enter",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"check-or-enter HTTP {resp.status_code}")
                return
            try:
                data = resp.json()
            except Exception as e:
                resp.failure(f"check-or-enter invalid JSON: {e}")
                return

            customer_id = data.get("customerId")
            jwt_token = data.get("jwtToken")
            if not customer_id or not jwt_token:
                resp.failure("check-or-enter missing customerId/jwtToken")
                return
            resp.success()

        # Step 3-4: SSE wait until READY
        sse_ready = self._wait_until_ready_sse(customer_id, ACTION_ID, timeout_s=SSE_TIMEOUT_SECONDS)
        if not sse_ready:
            resp.failure("sse was not ready")
            return

        # Step 5: verify with jwtToken
        with self.client.post(
            "/api/v1/customer/verify",
            headers={"X-GREENLIGHT-TOKEN": jwt_token},
            json={},
            name="/verify",
            catch_response=True,
        ) as verify_resp:
            if verify_resp.status_code == 200:
                verify_resp.success()
            else:
                verify_resp.failure(f"verify HTTP {verify_resp.status_code}")

        self.stop(True)
            
    def _wait_until_ready_sse(self, customer_id: str, action_id: str, timeout_s: float = 60.0) -> bool:
        """
        Connects to SSE and returns True once an event with waitStatus == 'READY' is observed,
        or False if timed out / failed. Reports one Locust request sample (type 'SSE').
        Example SSE line: data:{"customerId":"13:0MZV87KYZZY5T","position":4,"waitStatus":"WAITING"}
        """
        url = f"{self.host}/waiting/sse?actionId={action_id}&customerId={customer_id}"
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        start_perf = time.perf_counter()
        ok = False
        exc: Exception | None = None
        try:
            # Use requests streaming so we can parse SSE lines manually.
            # Keep connect/read timeouts bounded so we can bail out properly.
            with requests.get(url, headers=headers, stream=True, timeout=(5, timeout_s)) as r:
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=True):
                    # Keep checking timeout during long idle periods
                    if (time.perf_counter() - start_perf) > timeout_s:
                        break
                    if not raw:
                        continue
                    # SSE payload lines start with "data:"
                    if raw.startswith("data:"):
                        payload = raw[len("data:"):].strip()
                        try:
                            evt = json.loads(payload)
                        except Exception:
                            continue
                        if evt.get("waitStatus") == "READY":
                            ok = True
                            break
        except Exception as e:
            exc = e
        finally:
            # Record as a single "SSE" request in Locust stats
            elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
            events.request.fire(
                request_type="SSE",
                name="/waiting/sse",
                response_time=elapsed_ms,
                response_length=0,
                exception=None if ok else exc or Exception("SSE timeout or not READY"),
                context={},
                response=None,
            )
        return ok