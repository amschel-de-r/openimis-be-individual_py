import json
from dataclasses import dataclass
from django.utils.translation import gettext as _
from core.services import wait_for_mutation

from individual.models import Individual
from individual.tests.test_helpers import (
    create_individual,
    create_group,
    add_individual_to_group,
    IndividualGQLTestCase,
)

from social_protection.tests.test_helpers import (
    create_benefit_plan,
    add_individual_to_benefit_plan,
    add_group_to_benefit_plan,
)
from social_protection.models import BenefitPlan
from social_protection.apps import SocialProtectionConfig
from social_protection.services import BeneficiaryService, GroupBeneficiaryService

class EnrollmentGQLTest(IndividualGQLTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.benef_service = BeneficiaryService(cls.admin_user)
        cls.group_benef_service = GroupBeneficiaryService(cls.admin_user)

        def create_individual_with_params(name, num_children, able_bodied=None): 
            return create_individual(cls.admin_user.username, payload_override={
                'first_name': name,
                'json_ext': {
                    'number_of_children': num_children,
                    **({'able_bodied': able_bodied if able_bodied else {}})
                }
            })
        
        def create_group_with_params(name, num_children, able_bodied=None):
            group = create_group(cls.admin_user.username)
            for i in range(num_children + 1):
                ind = create_individual_with_params(name, num_children, able_bodied)
                add_individual_to_group(cls.admin_user.username, ind, group, is_head=i==0)
            return group
        
        def add_to_benefit_plan(plan, status="POTENTIAL", benefs=[]):
            for benef in benefs:
                if plan.type == "INDIVIDUAL":
                    add_individual_to_benefit_plan(cls.benef_service, benef, plan, payload_override={'status': status})
                else:
                    add_group_to_benefit_plan(cls.group_benef_service, benef, plan, payload_override={'status': status})

        cls.benefit_plan_indiv = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'SGQLBase', 'type': "INDIVIDUAL"
        })

        cls.benefit_plan_indiv_max_active_benefs = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'SQGLMax', 'type': "INDIVIDUAL", 'max_beneficiaries': 2
        })

        cls.benefit_plan_group = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'GGQLBase', 'type': "GROUP"
        })

        cls.benefit_plan_group_max_active_benefs = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'GQGLMax', 'type': "GROUP", 'max_beneficiaries': 2
        })

        cls.individual_2child = create_individual_with_params('TwoChildren', 2)
        cls.individual_1child = create_individual_with_params('OneChild', 1)
        cls.individual_able_bodied =  create_individual_with_params('OneChild Able bodied', 1, able_bodied=True)
        cls.individual =  create_individual_with_params('NoChild', 0)

        cls.group_2child = create_group_with_params('TwoChildren', 2)
        cls.group_1child = create_group_with_params('OneChild', 1)
        cls.group_able_bodied =  create_group_with_params('OneChild Able bodied', 1, able_bodied=True)
        cls.group =  create_group_with_params('NoChild', 0)

        add_to_benefit_plan(cls.benefit_plan_indiv, status="POTENTIAL", benefs=[cls.individual_able_bodied, cls.individual_1child])
        add_to_benefit_plan(cls.benefit_plan_indiv, status="ACTIVE", benefs=[cls.individual_2child])

        add_to_benefit_plan(cls.benefit_plan_indiv_max_active_benefs, status="POTENTIAL", benefs=[cls.individual_2child])
        add_to_benefit_plan(cls.benefit_plan_indiv_max_active_benefs, status="ACTIVE", benefs=[cls.individual_1child])

        add_to_benefit_plan(cls.benefit_plan_group, status="POTENTIAL", benefs=[cls.group_able_bodied, cls.group_1child])
        add_to_benefit_plan(cls.benefit_plan_group, status="ACTIVE", benefs=[cls.group_2child])

        add_to_benefit_plan(cls.benefit_plan_group_max_active_benefs, status="POTENTIAL", benefs=[cls.group_2child])
        add_to_benefit_plan(cls.benefit_plan_group_max_active_benefs, status="ACTIVE", benefs=[cls.group_1child])
        

        # EXPECTED OUTPUT (assumes individuals cannot be part of groups)
        # total, selected, any_plan, no_plan, selected_plan, all_plan_status, to_enroll, max_active_benefs_exceeded
        cls.ENROLLMENT_SUMMARY_KEYS = [
            "totalNumberOfIndividuals",
            "numberOfSelectedIndividuals",
            "numberOfIndividualsAssignedToProgramme",
            "numberOfIndividualsNotAssignedToProgramme",
            "numberOfIndividualsAssignedToSelectedProgramme",
            "numberOfIndividualsAssignedToSelectedProgrammeAndStatus",
            "numberOfIndividualsToUpload",
            "maxActiveBeneficiariesExceeded",
        ]

        cls.GROUP_ENROLLMENT_SUMMARY_KEYS = [
            "totalNumberOfGroups",
            "numberOfSelectedGroups",
            "numberOfGroupsAssignedToProgramme",
            "numberOfGroupsNotAssignedToProgramme",
            "numberOfGroupsAssignedToSelectedProgramme",
            "numberOfGroupsAssignedToSelectedProgrammeAndStatus",
            "numberOfGroupsToUpload",
            "maxActiveBeneficiariesExceeded",
        ]

        @dataclass
        class EnrollmentTestCase:
            benefit_plan: BenefitPlan
            status: str
            custom_filters: str
            
            @property
            def is_group(self):
                return self.benefit_plan.type == "GROUP"

            def expect_summary(self, *args):
                summary_keys = cls.GROUP_ENROLLMENT_SUMMARY_KEYS if self.is_group else cls.ENROLLMENT_SUMMARY_KEYS
                self.expected_summary = dict(zip(summary_keys, args))
                return self

        total_ind = 12
        total_group = 4
                
        cls.individual_enrollment_cases = [
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "ACTIVE", '[]').expect_summary(
                total_ind, 4, 3, 1, 2, 1, 2, True  # Active, exceeds limit
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "POTENTIAL", "[]").expect_summary(
                total_ind, 4, 3, 1, 3, 2, 1, False  # Basic, those in group not selected
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "POTENTIAL", "[]").expect_summary(
                total_ind, 4, 3, 1, 2, 1, 2, False  # Different plan check
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "POTENTIAL", '["able_bodied__exact__boolean=True"]').expect_summary(
                total_ind, 1, 1, 0, 1, 2, 0, False  # Filters shouldn't apply to 'numberOfIndividualsAssignedToSelectedProgrammeAndStatus'
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "ACTIVE", '[]').expect_summary(
                total_ind, 4, 3, 1, 3, 1, 1, False  # Active, no max benefs limit
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "ACTIVE", '["number_of_children__gte__integer=1"]').expect_summary(
                total_ind, 3, 3, 0, 2, 1, 1, False  # Active, filters, within limit
            ),
        ]

        cls.group_enrollment_cases = [
            EnrollmentTestCase(cls.benefit_plan_group_max_active_benefs, "ACTIVE", '[]').expect_summary(
                total_group, 4, 3, 1, 2, 1, 2, True  # Active, exceeds limit
            ),
            EnrollmentTestCase(cls.benefit_plan_group, "POTENTIAL", "[]").expect_summary(
                total_group, 4, 3, 1, 3, 2, 1, False  # Basic, those in group not selected
            ),
            EnrollmentTestCase(cls.benefit_plan_group_max_active_benefs, "POTENTIAL", "[]").expect_summary(
                total_group, 4, 3, 1, 2, 1, 2, False  # Different plan check
            ),
            EnrollmentTestCase(cls.benefit_plan_group, "POTENTIAL", '["able_bodied__exact__boolean=True"]').expect_summary(
                total_group, 1, 1, 0, 1, 2, 0, False  # Filters shouldn't apply to 'numberOfGroupsAssignedToSelectedProgrammeAndStatus'
            ),
            EnrollmentTestCase(cls.benefit_plan_group, "ACTIVE", '[]').expect_summary(
                total_group, 4, 3, 1, 3, 1, 1, False  # Active, no max benefs limit
            ),
            EnrollmentTestCase(cls.benefit_plan_group_max_active_benefs, "ACTIVE", '["number_of_children__gte__integer=1"]').expect_summary(
                total_group, 3, 3, 0, 2, 1, 1, False  # Active, filters, within limit
            ),
        ]
  
    def test_individual_enrollment_summary_query(self):
        newline = "\n\t"
        def send_individual_enrollment_summary_query(benefit_plan_id, status, custom_filters):
            query_str = f'''query {{
                individualEnrollmentSummary(
                    benefitPlanId: "{benefit_plan_id}"
                    status: "{status}"
                    customFilters: {custom_filters}
                ) {{
                {newline.join(self.ENROLLMENT_SUMMARY_KEYS)}
                }}
            }}'''

            return self.query(
                query_str,
                headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
            )
        
        for i, case in enumerate(self.individual_enrollment_cases):
            response = send_individual_enrollment_summary_query(case.benefit_plan.uuid, case.status, case.custom_filters)
            self.assertResponseNoErrors(response)
            summary = json.loads(response.content)['data']['individualEnrollmentSummary']
            for (key, expected_val) in case.expected_summary.items():
                self.assertTrue(key in summary.keys(), f"Expected summary to have key {key} but key not found")
                self.assertTrue(
                    summary[key] == expected_val,
                    f'Expected test case {i} to have {key} value {expected_val}, but got {summary[key]}'
                )

    def test_group_enrollment_summary_query(self):
        newline = "\n\t"
        def send_group_enrollment_summary_query(benefit_plan_id, status, custom_filters):
            query_str = f'''query {{
                groupEnrollmentSummary(
                    benefitPlanId: "{benefit_plan_id}"
                    status: "{status}"
                    customFilters: {custom_filters}
                ) {{
                {newline.join(self.GROUP_ENROLLMENT_SUMMARY_KEYS)}
                }}
            }}'''

            return self.query(
                query_str,
                headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
            )
        
        for i, case in enumerate(self.group_enrollment_cases):
            response = send_group_enrollment_summary_query(case.benefit_plan.uuid, case.status, case.custom_filters)
            self.assertResponseNoErrors(response)
            summary = json.loads(response.content)['data']['groupEnrollmentSummary']
            for (key, expected_val) in case.expected_summary.items():
                self.assertTrue(key in summary.keys(), f"Expected summary to have key {key} but key not found")
                self.assertTrue(
                    summary[key] == expected_val,
                    f'Expected test case {i} to have {key} value {expected_val}, but got {summary[key]}'
                )
