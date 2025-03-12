from locust import HttpUser, task, between
import datetime
import time

class waitingUser(HttpUser):

    # 상태 조회를 3초마다 실행하도록 wait_time을 설정
    wait_time = between(0.01,0.1)  # 상태 조회 후 3초 대기

    "이벤트 정보 조회 - 이벤트 조회만 admin api라 url다름"
    def on_start(self):
        event_name = "test" #이벤트명은 test로 고정

        # 이벤트 조회 GET 요청 보내기
        with self.client.get(f"/events/{event_name}", catch_response=True) as response:
            if response.status_code == 200:
                # back_pressure
                queue_back_pressure = response.json().get("queueBackpressure")  # queueBackpressure

                # 이벤트 시작, 종료 일자
                self.event_start_time = response.json().get("eventStartTime")
                self.event_end_time = response.json().get("eventEndTime")
                self.event_name = event_name

                # 현재 시간
                self.current_time = datetime.datetime.now().isoformat()

                # 현재 시간이 이벤트 시간 범위 내에 있으면 성공으로 처리
                if self.event_start_time <= self.current_time <= self.event_end_time:
                    print(f"[Greenlight Loadtest] event [{self.event_name}] is ongoing")
                    response.success()
                # 현재 시간이 이벤트 범위 밖이면 테스트 중단
                else:
                    print(f"[Greenlight Loadtest] event [{self.event_name}] is not active. Stop test.")
                    response.failure(f"[Greenlight Loadtest] event [{self.event_name}] is not active. Stop test.")
                    self.stop(True)

                response.success()  # 성공적으로 응답을 처리했다는 표시
            else:
                print(f"[Greenlight Loadtest] event [{self.event_name}] API fail.")
                response.failure(f"[Greenlight Loadtest] event [{self.event_name}] API fail.")  # 실패 기록


    "고객 등록 - Redis에 사용자 등록"
    @task
    def register_to_queue(self):
        payload = {
            "eventName": self.event_name
        }
        with self.client.post("/customers", json=payload, catch_response=True) as response:
            if response.status_code == 201: # 고객등록 정상 응답은 201
                self.customer_id = response.json().get("customerId")
                print(f"[Greenlight Loadtest] register queue success : customerid {self.customer_id} ")
                response.success()
                #사용자 등록 후 3초마다 상태 조회
                self.check_status()
            else:
                response.failure(f"[Greenlight Loadtest] register queue fail: {response.status_code}")


    "대기 상태 확인 - 대기/입장 여부"
    def check_status(self):
        while True:
            # 상태 조회 URL 호출
            with self.client.get(f"/customers/{self.customer_id}/status", catch_response=True) as response:
                if response.status_code == 200:
                    customer_status = response.json().get('waitingPhase')
                    print(f"[Greenlight Loadtest] Customer {self.customer_id} is {customer_status}.")

                    #입장 가능한 상태 -> 테스트 종료
                    if customer_status == 'READY' :
                        print(f"[Greenlight Loadtest] Customer {self.customer_id} is ready. Stopping the test.")
                        self.stop(True)
                    response.success()
                else:
                    response.failure(f"[Greenlight Loadtest] Failed to fetch status for customer {self.customer_id}")
                    print(f"[Greenlight Loadtest] Failed to fetch status for customer {self.customer_id}.")
            time.sleep(3)
