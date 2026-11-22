from django.test import SimpleTestCase
from django.urls import reverse


class HomePageTests(SimpleTestCase):
    def test_public_home_displays_the_marketplace(self):
        response = self.client.get(reverse("home"))

        self.assertTemplateUsed(response, "home.html")
        self.assertContains(response, "栖物")
        self.assertContains(response, "商品大厅")
