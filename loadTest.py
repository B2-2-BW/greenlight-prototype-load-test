from locust import HttpUser, task, between

class MyLoadTestUser(HttpUser):
    # 사용자 사이의 대기 시간 (1초 ~ 5초)
    wait_time = between(1, 5)

    @task(1)
    def test_my_api(self):
        self.client.get("http://localhost:23080/events")