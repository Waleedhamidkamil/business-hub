"""Deliberately break audited behavior in memory and require its test to fail."""
from pathlib import Path
import importlib.util
import io
import unittest
from unittest.mock import patch

path=Path(__file__).with_name('lpo_audit_fixes.test.py')
spec=importlib.util.spec_from_file_location('lpo_regressions',path)
reg=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)
from backend import lpo_billing

row=reg.database._lpo_row
save=reg.database.save_lpo
seed=reg.api.Api.lpo_document_seed
def no_rows(raw):
    result=row(raw);result.pop('rows',None);return result
def cleared_header(*args,**kwargs):
    if kwargs.get('rows') is None:kwargs['rows']=[]
    return save(*args,**kwargs)
def delivery_seed(self,client,number,document_kind='dn'):
    return seed(self,client,number,'dn')
def full_order_again(self,client,number,document_kind='dn'):
    result=seed(self,client,number,document_kind)
    if result['ok'] and not result['rows']:result['rows']=reg.ORDER
    return result
def guessed_quantity(self,client,number,document_kind='dn'):
    result=seed(self,client,number,document_kind)
    for item in result['rows']:
        if item['qty']=='':item['qty']=100
    return result
controls=[
    ('decoded lines omitted',reg.database,'_lpo_row',no_rows,'test_list_and_get_expose_saved_lines'),
    ('header save clears lines',reg.database,'save_lpo',cleared_header,'test_a_header_only_save_preserves_lines'),
    ('invoice reuses delivery remainder',reg.api.Api,'lpo_document_seed',delivery_seed,'test_invoice_uses_delivered_unbilled_items'),
    ('full order offered again',reg.api.Api,'lpo_document_seed',full_order_again,'test_settled_items_are_not_offered_again'),
    ('foreign quote validation removed',reg.api.Api,'_lpo_quote_client_error',staticmethod(lambda *a,**k:''),'test_foreign_internal_quote_is_refused_on_save'),
    ('partial final month dropped',reg.database,'_term_months',lambda a,b:max(1,(b.year-a.year)*12+b.month-a.month-(b.day<a.day)),'test_partial_last_rental_month_counts'),
    ('service dates ignored',lpo_billing,'_service_ranges',lambda rows:[],'test_service_period_is_used_instead_of_invoice_month'),
    ('confirmed period not stored',lpo_billing,'save_period',lambda *a:{'ok':True},'test_confirmed_period_keeps_the_invoice_unchanged'),
    ('reference gaps suppressed',lpo_billing,'reference_gaps',lambda:[],'test_reference_review_is_scoped_and_read_only'),
    ('rental quantity guessed',reg.api.Api,'lpo_document_seed',guessed_quantity,'test_recurring_invoice_does_not_guess_period_quantity'),
]
failures=0
for name,target,attribute,mutant,test in controls:
    with patch.object(target,attribute,mutant):
        result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([reg.AuditFixes(test)]))
    caught=bool(result.failures) and not result.errors
    print(('PASS caught: ' if caught else 'FAIL control: ')+name)
    failures+=not caught
print(f'LPO mutation controls: {len(controls)-failures}/{len(controls)} caught')
raise SystemExit(bool(failures))
