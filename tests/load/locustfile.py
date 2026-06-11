from locust import HttpUser, task

class TestUser(HttpUser):
  @task
  def test01(self):
    self.client.get("/streamers")

  # @task
  # def test02(self):
  #   self.client.get("/about")
  #
  # @task
  # def test03(self):
  #   self.client.get("/")
  #
  # @task
  # def test04(self):
  #   self.client.get("/memealerts-tutorial")
  #
  # @task
  # def test05(self):
  #   self.client.get("/kinda_roadmap")
  #
  # @task
  # def test06(self):
  #   self.client.get("/faq")

  @task
  def test07(self):
    self.client.get("/profile/quantum075")
