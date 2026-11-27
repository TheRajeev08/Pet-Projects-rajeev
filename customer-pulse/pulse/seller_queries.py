"""Read-only seller feeds. Column contracts verified against Revenue MCP on 2026-11-27.

Each query is account-scoped and packs bounded rows per account. The collector rejects
truncated bags. Invoice charges are additive; snapshot metrics and distinct actors are not.
"""

FEEDS = {
    "invoices": ("rev_source", "salesforce_account_monthly_consumption_invoices_fact", "salesforce_account_id",
                 "service_month >= startofmonth(datetime_add('month', -13, today)) and service_month < startofmonth(today)",
                 """| join kind=leftouter (consumption_product_dim | project consumption_product_id, consumption_product_name) on consumption_product_id
| summarize amount=sum(charge_amt), amount_n=countif(isnotnull(charge_amt)), units=sum(billed_units), units_n=countif(isnotnull(billed_units)), source_date=max(todatetime(dag_last_run_at))
    by account_id=salesforce_account_id, ['date']=startofmonth(service_month), product_id=consumption_product_id, product=coalesce(consumption_product_name, product_name_from_invoice), uom, billing_source
| extend amount=iff(amount_n > 0, amount, real(null)), units=iff(units_n > 0, units, real(null))"""),
    "actions": ("c360", "fact_actions_metrics", "entity_id", "day >= today - 90d and day <= today",
                """| project account_id=entity_id, ['date']=day, source_date=day, active_days=num_days_using_actions_past_28_days,
minutes=num_minutes_past_28_days, jobs=num_jobs_past_28_days, hosted_minutes=num_minutes_github_hosted_past_28_days,
self_hosted_minutes=num_minutes_self_hosted_past_28_days, hosted_jobs=num_jobs_github_hosted_past_28_days,
self_hosted_jobs=num_jobs_self_hosted_past_28_days, minutes_14d=num_minutes_past_14_days,
minutes_prior14=num_minutes_previous_14_days, jobs_14d=num_jobs_past_14_days, jobs_prior14=num_jobs_previous_14_days"""),
    "protection": ("c360", "fact_ghas_repo_metrics", "entity_id", "day >= today - 30d and day <= today",
                   """| summarize arg_max(day, *) by entity_id
| project account_id=entity_id, ['date']=day, source_date=day, private_repos=private_repo_count,
code_repos=code_scanning_enabled_private_repo_count, secret_repos=secret_scanning_enabled_private_repo_count,
commits=commits_all_repositories, code_commits=commits_repositories_with_cse, secret_commits=commits_repositories_with_sse"""),
    "model_cost": ("rev_source", "enterprise_account_monthly_ubb_model_cost_fact", "salesforce_account_id",
                   "todatetime(measurement_month_start_date) >= startofmonth(datetime_add('month', -13, today)) and todatetime(measurement_month_start_date) < startofmonth(today)",
                   """| join kind=leftouter (ubb_integration_model_dim | project ubb_integration_model_id, integration, model) on ubb_integration_model_id
| project account_id=salesforce_account_id, ['date']=todatetime(measurement_month_start_date), source_date=todatetime(measurement_month_end_date),
enterprise_id=enterprise_account_id, model_id=ubb_integration_model_id, model, integration,
amount=total_net_amount, actors=total_distinct_actor_count, units=total_quantity"""),
    "selfserve": ("rev_source", "salesforce_account_monthly_self_serve_github_team_revenue_fact", "salesforce_account_id",
                  "measurement_date == toscalar(salesforce_account_monthly_self_serve_github_team_revenue_fact | summarize max(measurement_date))",
                  """| project account_id=salesforce_account_id, ['date']=measurement_date, source_date=measurement_date,
billing_type, seats=teams_seats, arr=teams_license_current_arr, mrr=teams_license_mrr"""),
    "mrr": ("rev_source", "salesforce_account_monthly_mrr_fact", "salesforce_account_id",
             "measurement_month == toscalar(salesforce_account_monthly_mrr_fact | summarize max(measurement_month))",
             """| project account_id=salesforce_account_id, ['date']=measurement_month, source_date=todatetime(snapshot_day),
ghe_license_mrr, ghe_metered_mrr, ghas_license_mrr, ghas_metered_mrr, ghcs_license_mrr, ghcs_metered_mrr,
ghsp_license_mrr, ghsp_metered_mrr, copilot_business_mrr, copilot_enterprise_mrr, copilot_standalone_mrr"""),
    "opportunities": ("rev_source", "salesforce_account_opportunity_licenses_fact", "salesforce_account_id", "true",
                      """| join kind=leftouter (opportunity_dim | project opportunity_id, opportunity_name, opportunity_stage, opportunity_close_date, opportunity_type, is_test_opportunity) on opportunity_id
| where isnull(is_test_opportunity) or is_test_opportunity == false
| where opportunity_close_date >= today - 395d or opportunity_stage !startswith 'Closed'
| project account_id=salesforce_account_id, id=opportunity_id, title=opportunity_name, status=opportunity_stage, ['type']=opportunity_type,
['date']=opportunity_close_date, source_date=todatetime(dag_last_run_at), expected_arr=total_expected_license_arr,
new_arr=license_new_arr, upsell_arr=license_upsell_arr, contraction_arr=license_contraction_arr,
churn_arr=license_churn_arr, net_arr=license_net_change_in_arr"""),
    "support": ("rev_source", "salesforce_account_support_tickets_fact", "salesforce_account_id", "true",
                 """| join kind=leftouter (support_ticket_dim | project ticket_id, status, priority=current_priority, title=subject, refreshed=todatetime(dag_last_run_at)) on ticket_id
| where created_date >= today - 365d or status !in ('solved', 'closed')
| project account_id=salesforce_account_id, id=tostring(ticket_id), ['date']=created_date, source_date=refreshed, status, priority, ['title'],
first_response_minutes=reply_time_initial_response, resolution_minutes=full_resolution_time_in_minutes_calendar,
handle_hours=handle_time_hours, csat=experience_csat_score, solved_date"""),
    "services": ("rev_source", "salesforce_account_opportunity_services_fact", "account_id", "true",
                  """| join kind=leftouter (opportunity_dim | project opportunity_id, opportunity_close_date, opportunity_name) on opportunity_id
| project account_id, id=opportunity_id, title=opportunity_name, ['date']=opportunity_close_date,
source_date=todatetime(dag_last_run_at), booked=total_booked, expected=total_expected_gh_revenue"""),
    "retention": ("c360", "fact_arr_retention_metrics", "entity_id", "day >= today - 30d and day <= today",
                   """| summarize arg_max(day, *) by entity_id, retention_metrics_start_period, retention_metrics_end_period, arr_type, arr_segment
| where retention_metrics_end_period >= today - 395d
| project account_id=entity_id, source_date=day, ['date']=retention_metrics_end_period, start=retention_metrics_start_period,
arr_type, arr_segment, ndrr, gdrr, starting_arr=starting_period_arr, ending_arr=ending_period_arr"""),
    "risks": ("gainsight", "risk_cta", "sfdc_account_id", "todatetime(day) >= today - 30d and todatetime(day) <= today",
               """| extend snapshot=todatetime(day)
| summarize arg_max(snapshot, *) by gsid
| project account_id=sfdc_account_id, id=gsid, title=cta_name, ['date']=created_date, source_date=snapshot,
closed_date, status, priority=risk_level, reason, product=str_product_at_risk, arr_at_risk=co_risk_forecast,
renewal_arr_at_risk=renewals_risk_forecast, opportunity_url=salesforce_opportunity_link, support_url=zendesk_link"""),
    "sentiment": ("gainsight", "customer_sentiment_measures", "sfdc_account_id",
                   "todatetime(day) >= today - 90d and todatetime(day) <= today",
                   """| project account_id=sfdc_account_id, ['date']=todatetime(day), source_date=todatetime(day), name, label, score,
trend=iff(trend in ('-1','0','1'), trend, '')"""),
    "success": ("gainsight", "success_plan", "company_sfdc_id", "todatetime(day) >= today - 30d and todatetime(day) <= today",
                 """| extend snapshot=todatetime(day)
| summarize arg_max(snapshot, *) by success_plan_gsid
| project account_id=company_sfdc_id, id=success_plan_gsid, source_date=snapshot, ['date']=plan_modified_date,
status, due=plan_due_date, objective=customer_business_objective__pt, ghe_goals=ghe_measurable_goals__pt,
security_goals=ghas_measurable_goals__pt, copilot_goals=copilot_measurable_goals__pt,
actions_goals=actions_measurable_goals__pt, url=plan_slides_link"""),
    "timeline": ("gainsight", "timeline_activity", "company_sfdc_id",
                  "todatetime(day) >= today - 30d and todatetime(day) <= today",
                  """| extend snapshot=todatetime(day)
| summarize arg_max(snapshot, *) by activity_gsid
| where activity_date >= today - 180d and activity_date <= today
| project account_id=company_sfdc_id, id=activity_gsid, title=subject, ['date']=activity_date, source_date=snapshot,
['type'], attendees=external_attendees, internal_attendees, products=str_products_discussed, notes=notes__pt"""),
}

LABELS = {
    "invoices": "Consumption invoices", "actions": "Actions usage (experimental)",
    "protection": "Repository protection (experimental)", "model_cost": "Copilot model cost",
    "selfserve": "Self-serve Team", "mrr": "Recurring revenue", "opportunities": "License opportunities",
    "support": "Support tickets", "services": "Services bookings", "retention": "Revenue retention (experimental)",
    "risks": "Gainsight risks", "sentiment": "Gainsight sentiment", "success": "Success plans",
    "timeline": "Gainsight timeline", "contacts": "Salesforce contacts", "issues": "SE linked issues",
    "pocs": "Proofs of concept", "gong": "Gong calls", "notes": "Account notes", "partners": "Partners",
    "hierarchy": "Child accounts", "github_accounts": "Enterprise mapping", "budgets": "Enterprise budgets", "emails": "Outlook email metadata",
    "meetings": "Outlook meetings", "tech_stack": "Tech stack",
}


def render(feed, ids, today, nonce):
    import json
    from .queries import SFDC_ID

    if not ids or any(not SFDC_ID.fullmatch(aid) for aid in ids):
        raise ValueError("Seller queries require canonical Salesforce account IDs")
    db, table, account, window, projection = FEEDS[feed]
    query = (f"// seller:{feed} run:{nonce}\nlet today=datetime({today});\n"
             f"{table}\n| where {account} in (dynamic({json.dumps(ids)}))\n| where {window}\n{projection}\n"
             "| order by ['date'] asc\n| summarize row_count=count(), data=make_list(pack_all(), 10000), as_of=max(source_date) by account_id")
    return {"database": db, "query": query}
