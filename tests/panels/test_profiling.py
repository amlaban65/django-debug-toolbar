import sys
import threading
import unittest

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test.utils import override_settings

from debug_toolbar.panels.profiling import ProfilingPanel
from debug_toolbar.toolbar import DebugToolbar

from ..base import BaseTestCase, IntegrationTestCase, rf
from ..views import listcomp_view, regular_view


@override_settings(
    DEBUG_TOOLBAR_PANELS=["debug_toolbar.panels.profiling.ProfilingPanel"]
)
class ProfilingPanelTestCase(BaseTestCase):
    panel_id = ProfilingPanel.panel_id

    def test_regular_view(self):
        self._get_response = lambda request: regular_view(request, "profiling")
        response = self.panel.process_request(self.request)
        self.panel.generate_stats(self.request, response)
        self.assertIn("func_list", self.panel.get_stats())
        self.assertIn("regular_view", self.panel.content)

    def test_insert_content(self):
        """
        Test that the panel only inserts content after generate_stats and
        not the process_request.
        """
        self._get_response = lambda request: regular_view(request, "profiling")
        response = self.panel.process_request(self.request)
        # ensure the panel does not have content yet.
        self.assertNotIn("regular_view", self.panel.content)
        self.panel.generate_stats(self.request, response)
        self.reload_stats()
        # ensure the panel renders correctly.
        content = self.panel.content
        self.assertIn("regular_view", content)
        self.assertIn("render", content)
        self.assertValidHTML(content)
        # ensure traces aren't escaped
        self.assertIn('<span class="djdt-path">', content)

    @override_settings(DEBUG_TOOLBAR_CONFIG={"PROFILER_THRESHOLD_RATIO": 1})
    def test_cum_time_threshold(self):
        """
        Test that cumulative time threshold excludes calls
        """
        self._get_response = lambda request: regular_view(request, "profiling")
        response = self.panel.process_request(self.request)
        self.panel.generate_stats(self.request, response)
        # ensure the panel renders but doesn't include our function.
        content = self.panel.content
        self.assertIn("regular_view", content)
        self.assertNotIn("render", content)
        self.assertValidHTML(content)

    @unittest.skipUnless(
        sys.version_info < (3, 12, 0),
        "Python 3.12 no longer contains a frame for list comprehensions.",
    )
    def test_listcomp_escaped(self):
        self._get_response = lambda request: listcomp_view(request)
        response = self.panel.process_request(self.request)
        self.panel.generate_stats(self.request, response)
        content = self.panel.content
        self.assertNotIn('<span class="djdt-func"><listcomp></span>', content)
        self.assertIn('<span class="djdt-func">&lt;listcomp&gt;</span>', content)

    def test_generate_stats_no_profiler(self):
        """
        Test generating stats with no profiler.
        """
        response = HttpResponse()
        self.assertIsNone(self.panel.generate_stats(self.request, response))

    @unittest.skipIf(
        sys.version_info < (3, 12), "only one profiler at a time since 3.12"
    )
    def test_overlapping_request_is_not_profiled(self):
        """
        A request that overlaps one already being profiled runs its view once,
        unprofiled, instead of raising ValueError from cProfile.
        """
        in_view = threading.Event()
        release = threading.Event()
        errors = []

        def slow_view(request):
            in_view.set()
            release.wait(5)
            return HttpResponse()

        def first_request():
            toolbar = DebugToolbar(rf.get("/"), slow_view)
            try:
                toolbar.get_panel_by_id(self.panel_id).process_request(toolbar.request)
            except ValueError as exc:
                errors.append(exc)

        thread = threading.Thread(target=first_request)
        thread.start()
        try:
            self.assertTrue(in_view.wait(5))
            calls = []
            self._get_response = lambda request: calls.append(request) or HttpResponse()
            response = self.panel.process_request(self.request)
        finally:
            release.set()
            thread.join(5)

        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.panel.generate_stats(self.request, response)
        self.assertEqual(self.panel.get_stats(), {"skipped": True})
        self.assertIn("was not profiled", self.panel.content)

    def test_generate_stats_no_root_func(self):
        """
        Test generating stats using profiler without root function.
        """
        response = self.panel.process_request(self.request)
        self.panel.profiler.clear()
        self.panel.profiler.enable()
        self.panel.profiler.disable()
        self.panel.generate_stats(self.request, response)
        self.assertNotIn("func_list", self.panel.get_stats())


@override_settings(
    DEBUG=True, DEBUG_TOOLBAR_PANELS=["debug_toolbar.panels.profiling.ProfilingPanel"]
)
class ProfilingPanelIntegrationTestCase(IntegrationTestCase):
    def test_view_executed_once(self):
        self.assertEqual(User.objects.count(), 0)

        response = self.client.get("/new_user/")
        self.assertContains(response, "Profiling")
        self.assertEqual(User.objects.count(), 1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            response = self.client.get("/new_user/")
        self.assertEqual(User.objects.count(), 1)
