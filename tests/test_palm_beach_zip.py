"""Situs/mailing separation and safe correction regressions; no live traffic."""
import json
import unittest
from unittest.mock import Mock, patch
import app

PCN = '56434229410000080'
ADDRESS = '1256 W 30TH ST 1'

class PalmBeachZIP(unittest.TestCase):
    def response(self, rows, **extra):
        return json.dumps(dict(features=[{'attributes': {'PCN': p, 'ZIP_CODE': z}} for p,z in rows], **extra))

    def test_multiple_units_same_zip(self):
        with patch.object(app, 'http_get', return_value=self.response([(PCN,'33404')]*4)) as get:
            self.assertEqual(app.get_palm_beach_situs_zip(Mock(), PCN), '33404')
            self.assertIn('PCN%3D%27'+PCN, get.call_args.args[1])

    def test_missing_invalid_or_ambiguous_stays_unknown(self):
        for rows in [[], [(PCN,None)], [(PCN,'00000')], [(PCN,'3340')], [(PCN,'33404'),(PCN,'33407')], [(PCN,'33404'),(PCN,None)]]:
            with self.subTest(rows=rows), patch.object(app, 'http_get', return_value=self.response(rows)):
                self.assertEqual(app.get_palm_beach_situs_zip(Mock(),PCN),'')

    def test_wrong_parcel_and_partial_response_rejected(self):
        for body in [self.response([('00000000000000000','33404')]), self.response([(PCN,'33404')],exceededTransferLimit=True), json.dumps({'error':{'code':500}})]:
            with patch.object(app,'http_get',return_value=body), self.assertRaises(ValueError):
                app.get_palm_beach_situs_zip(Mock(),PCN)

    def test_invalid_parcel_no_request(self):
        with patch.object(app,'http_get') as get:
            self.assertEqual(app.get_palm_beach_situs_zip(Mock(),'123'),'')
            get.assert_not_called()

    def page(self,location=ADDRESS):
        return 'var model = '+json.dumps({'propertyDetail': {'PCN': PCN,'Location':location,'Municipality':'RIVIERA BEACH','AddressLine1':'1075 BIG TORCH ST','AddressLine3':'RIVIERA BEACH FL 33407 1140'}})+';'

    def test_example_owner_mailing_zip_never_used(self):
        with patch.object(app,'http_get',return_value=self.page()), patch.object(app,'get_palm_beach_situs_zip',return_value='33404'):
            result=app.get_palm_beach_property_appraiser_data(Mock(),PCN)
            self.assertEqual(result['zip'],'33404')
            self.assertEqual(result['address'],ADDRESS)
            self.assertEqual(result['zip_source'],app.PALM_BEACH_SITUS_QUERY_URL)

    def test_lookup_failure_does_not_fall_back_to_owner(self):
        with patch.object(app,'http_get',return_value=self.page()), patch.object(app,'get_palm_beach_situs_zip',side_effect=TimeoutError):
            result=app.get_palm_beach_property_appraiser_data(Mock(),PCN)
            self.assertEqual(result['zip'],'')
            self.assertEqual(result['zip_source'],'')

    def test_missing_situs_does_not_substitute_mail(self):
        with patch.object(app,'http_get',return_value=self.page('')), patch.object(app,'get_palm_beach_situs_zip',return_value='33404'):
            self.assertEqual(app.get_palm_beach_property_appraiser_data(Mock(),PCN)['address'],'')

    def test_nonblank_correction_requires_verified_same_address(self):
        row={'address':ADDRESS,'zip':'33404','property_appraiser':{'address':ADDRESS,'zip':'33404','zip_source':app.PALM_BEACH_SITUS_QUERY_URL}}
        self.assertTrue(app.palm_beach_verified_zip_change('palm_beach',ADDRESS,'33407',row))
        self.assertFalse(app.palm_beach_verified_zip_change('duval',ADDRESS,'33407',row))
        self.assertFalse(app.palm_beach_verified_zip_change('palm_beach','1075 BIG TORCH ST','33407',row))
        self.assertFalse(app.palm_beach_verified_zip_change('palm_beach',ADDRESS,'33404',row))
        row['property_appraiser']['zip_source']='mailing'
        self.assertFalse(app.palm_beach_verified_zip_change('palm_beach',ADDRESS,'33407',row))

if __name__ == '__main__':unittest.main()

class PalmBeachImportZIP(unittest.TestCase):
    def test_import_corrects_nonblank_zip_only_once_without_touching_other_fields(self):
        conn=Mock();cur=Mock();conn.cursor.return_value.__enter__=Mock(return_value=cur);conn.cursor.return_value.__exit__=Mock(return_value=False)
        row={'case_number':'3207TD','address':ADDRESS,'city':'RIVIERA BEACH','zip':'33404','property_appraiser':{'address':ADDRESS,'zip':'33404','zip_source':app.PALM_BEACH_SITUS_QUERY_URL}}
        current=[2747,'RIVIERA BEACH','33407',ADDRESS]
        def execute(sql,args=None):
            if 'UPDATE properties' in sql:
                self.assertIn('SET zip = %s, updated_at = NOW()',sql)
                self.assertNotIn('notes',sql);self.assertNotIn('stage',sql);self.assertNotIn('deleted_at =',sql)
                self.assertEqual(args,('33404',2747));current[2]=args[0]
        cur.execute.side_effect=execute;cur.fetchone.side_effect=lambda:tuple(current)
        with patch.object(app,'get_db_connection',return_value=conn),patch.object(app,'initialize_import_tables'),patch.object(app,'validate_properties_schema'),patch.object(app,'mark_import_property_seen'):
            first=app.import_scraped_properties('palm_beach',[row]);second=app.import_scraped_properties('palm_beach',[row])
        self.assertEqual(first['errors'],0);self.assertEqual(second['errors'],0)
        self.assertEqual(first['imported'],0);self.assertEqual(first['already_present'],1)
        self.assertEqual(sum('UPDATE properties' in c.args[0] for c in cur.execute.call_args_list),1)

    def test_case_insensitive_address_comparison_preserves_letters(self):
        self.assertTrue(app.same_address('1256 W 30th St 1',ADDRESS))
        self.assertFalse(app.same_address('123 oak st','123 elm st'))
