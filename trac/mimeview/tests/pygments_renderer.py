# -*- coding: utf-8 -*-
#
# Copyright (C) 2006 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.

import os
import re
import unittest

from genshi.core import Stream
from genshi.input import HTMLParser, XML

from trac.test import EnvironmentStub, Mock

from trac.mimeview.api import Mimeview
from trac.mimeview.pygments_renderer import PygmentsRenderer
from trac.web.chrome import Chrome
from trac.web.href import Href


class PygmentsRendererTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(enable=[Chrome, PygmentsRenderer])
        self.pygments = Mimeview(self.env).renderers[0]
        self.req = Mock(base_path='',chrome={},
                        abs_href=Href('/'), href=Href('/'),
                        session={}, perm=None, authname=None, tz=None)
        pygments_html = open(os.path.join(os.path.split(__file__)[0],
                                       'pygments.html'))
        self.pygments_html = Stream(list(HTMLParser(pygments_html)))

    def _expected(self, expected_id):
        return self.pygments_html.select('//div[@id="%s"]/*' % expected_id)

    def _test(self, expected_id, result):
        expected = str(self._expected(expected_id))
        result = str(result)
        expected, result = expected.splitlines(), result.splitlines()
        for exp, res in zip(expected, result):
            self.assertEquals(exp, res)
        self.assertEquals(len(expected), len(result))

    def test_python_hello(self):
        """
        Simple Python highlighting with Pygments (direct)
        """
        result = self.pygments.render(self.req, 'text/x-python', """
def hello():
        return "Hello World!"
""")
        self.assertTrue(result)
        self._test('python_hello', result)

    def test_python_hello_mimeview(self):
        """
        Simple Python highlighting with Pygments (through Mimeview.render)
        """
        result = mimeview = Mimeview(self.env).render(self.req,
                                                      'text/x-python', """
def hello():
        return "Hello World!"
""")
        self.assertTrue(result)
        self._test('python_hello_mimeview', result)

    def test_empty_content(self):
        """
        Simple test for direct rendering of empty content.
        """
        result = self.pygments.render(self.req, 'text/x-python', '')
        self.assertTrue(result)
        self._test('empty_content', result)


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(PygmentsRendererTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')