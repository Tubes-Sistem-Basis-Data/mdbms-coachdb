import tempfile
import unittest
from unittest.mock import Mock, patch

from common import ColumnSchema, DataType, RID, Row, TableNotFoundError
from storage_manager.catalog import Catalog
from storage_manager.facade import StorageManager
from storage_manager.page import SlottedPage
from storage_manager.stats import StatisticsManager


class TestStatistics(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(None)
        self.catalog.create_table('t', (ColumnSchema('id', DataType.INTEGER),
                                      ColumnSchema('value', DataType.VARCHAR, 10)), ('id',), ())
        self.rows = [Row(RID('t', 0, 0), [1, 'a']), Row(RID('t', 0, 1), [2, 'a']),
                     Row(RID('t', 9, 0), [3, None])]
        self.scan = Mock(side_effect=lambda table: iter(self.rows))
        self.stats = StatisticsManager(self.catalog, self.scan)

    def test_exact_contract_values_and_copy(self):
        stats = self.stats.get_stats('t')
        self.assertEqual(vars(stats), dict(table='t', n_r=3, b_r=2, l_r=17.0,
                                          f_r=226, distinct={'id': 3, 'value': 1}))
        stats.distinct.clear()
        self.assertEqual(self.stats.get_stats('t').distinct, {'id': 3, 'value': 1})
        self.assertEqual(self.scan.call_count, 1)

    def test_empty_table_and_missing(self):
        self.rows.clear()
        stats = self.stats.get_stats('t')
        self.assertEqual((stats.n_r, stats.b_r, stats.distinct), (0, 0, {'id': 0, 'value': 0}))
        self.assertEqual(stats.l_r, 17.0)
        self.assertEqual(stats.f_r, 226)
        with self.assertRaises(TableNotFoundError):
            self.stats.get_stats('missing')

    def test_mutations_and_repeated_recovery_notifications(self):
        self.stats.get_stats('t')
        self.rows.append(Row(RID('t', 9, 1), [4, 'b']))
        self.stats.on_insert('t')
        self.stats.on_insert('t')
        self.assertEqual(self.stats.get_stats('t').n_r, 4)
        self.rows[0].values[1] = 'c'
        self.stats.invalidate('t')
        self.assertEqual(self.stats.get_stats('t').distinct['value'], 3)
        self.rows[:] = self.rows[:2]
        self.stats.on_delete('t')
        self.stats.on_delete('t')
        stats = self.stats.get_stats('t')
        self.assertEqual((stats.n_r, stats.b_r), (2, 1))
        self.rows.clear()
        self.stats.recount('t')
        self.assertEqual(self.stats.get_stats('t').n_r, 0)

    def test_failed_recount_does_not_publish_partial_results(self):
        self.stats.get_stats('t')
        self.scan.side_effect = RuntimeError('scan failed')
        with self.assertRaises(RuntimeError):
            self.stats.recount('t')
        self.assertNotIn('t', self.stats._cache)
        self.assertNotIn('t', self.stats._row_count)

    def test_capacity_includes_header_and_status(self):
        with patch('common.config.BLOCK_SIZE', 100):
            self.assertEqual(SlottedPage.capacity(9), 8)
            self.assertEqual(SlottedPage.capacity(79), 1)
            self.assertEqual(SlottedPage.capacity(80), 0)

    def test_facade_recounts_all_tables_on_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            sm = StorageManager(directory)
            for name in ('a', 'b'):
                sm._catalog.create_table(name, (ColumnSchema('id', DataType.INTEGER),), (), ())
            rows = {'a': [Row(RID('a', 0, 0), [1])], 'b': []}
            sm._records.scan = lambda table: iter(rows[table])
            self.assertEqual(sm.get_stats('a').n_r, 1)
            rows['a'].clear()
            rows['b'].append(Row(RID('b', 0, 0), [2]))
            sm._index_manager.rebuild_all = Mock()
            sm.rebuild_indexes()
            sm._index_manager.rebuild_all.assert_called_once()
            self.assertEqual(sm.get_stats('a').n_r, 0)
            self.assertEqual(sm.get_stats('b').n_r, 1)
