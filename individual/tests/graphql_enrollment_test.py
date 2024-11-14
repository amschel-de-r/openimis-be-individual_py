import json
from dataclasses import dataclass
from django.utils.translation import gettext as _
from core.services import wait_for_mutation

from individual.models import Individual
from individual.tests.test_helpers import (
    create_individual,
    create_group_with_individual,
    IndividualGQLTestCase,
)

from social_protection.tests.test_helpers import (
    create_benefit_plan,
    add_individual_to_benefit_plan
)
from social_protection.models import BenefitPlan
from social_protection.apps import SocialProtectionConfig
from social_protection.services import BeneficiaryService

class EnrollmentGQLTest(IndividualGQLTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.individual_a, cls.group_a, _ = create_group_with_individual(cls.admin_user.username)
 
        create_individual_with_params = lambda name, num_children, able_bodied=None: create_individual(cls.admin_user.username, payload_override={
            'first_name': name,
            'json_ext': {
                'number_of_children': num_children,
                **({'able_bodied': able_bodied if able_bodied else {}})
            }
        })

        cls.individual_2child = create_individual_with_params('TwoChildren', 2)
        cls.individual_1child = create_individual_with_params('OneChild', 1)
        cls.individual_able_bodied =  create_individual_with_params('OneChild Able bodied', 1, True)
        cls.individual =  create_individual_with_params('NoChild', 0)

        cls.benef_service = BeneficiaryService(cls.admin_user)

        cls.benefit_plan_indiv = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'SGQLBase',
            'type': "INDIVIDUAL"
        })

        add_individual_to_benefit_plan(cls.benef_service, cls.individual_able_bodied, cls.benefit_plan_indiv)
        add_individual_to_benefit_plan(cls.benef_service, cls.individual_1child, cls.benefit_plan_indiv)
        add_individual_to_benefit_plan(cls.benef_service, cls.individual_2child, cls.benefit_plan_indiv, payload_override={'status': 'ACTIVE'})

        cls.benefit_plan_indiv_max_active_benefs = create_benefit_plan(cls.admin_user.username, payload_override={
            'code': 'SQGLMax',
            'type': "INDIVIDUAL",
            'max_beneficiaries': 2
        })
        add_individual_to_benefit_plan(cls.benef_service, cls.individual_2child, cls.benefit_plan_indiv_max_active_benefs)
        add_individual_to_benefit_plan(cls.benef_service, cls.individual_1child, cls.benefit_plan_indiv_max_active_benefs, payload_override={'status': 'ACTIVE'})

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

        @dataclass
        class EnrollmentTestCase:
            benefit_plan: BenefitPlan
            status: str
            custom_filters: str

            def expect_summary(self, *args):
                self.expected_summary = dict(zip(cls.ENROLLMENT_SUMMARY_KEYS, args))
                return self

        total = "5"
                
        cls.individual_enrollment_cases = [
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "ACTIVE", '[]').expect_summary(
                total, "4", "3", "1", "2", "1", "2", True  # Active, exceeds limit
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "POTENTIAL", "[]").expect_summary(
                total, "4", "3", "1", "3", "2", "1", False  # Basic, those in group not selected
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "POTENTIAL", "[]").expect_summary(
                total, "4", "3", "1", "2", "1", "2", False  # Different plan check
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "POTENTIAL", '["able_bodied__exact__boolean=True"]').expect_summary(
                total, "1", "1", "0", "1", "2", "0", False  # Filters shouldn't apply to 'numberOfIndividualsAssignedToSelectedProgrammeAndStatus'
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv, "ACTIVE", '[]').expect_summary(
                total, "4", "3", "1", "3", "1", "1", False  # Active, no max benefs limit
            ),
            EnrollmentTestCase(cls.benefit_plan_indiv_max_active_benefs, "ACTIVE", '["number_of_children__gte__integer=1"]').expect_summary(
                total, "3", "3", "0", "2", "1", "1", False  # Active, filters, within limit
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
    
    def test_confirm_individual_enrollment(self):
        def send_confirm_individual_enrollment_mutation(benefit_plan_id, status, custom_filters):
            query_str = f'''
                mutation {{
                  confirmIndividualEnrollment(
                    input: {{
                      customFilters: {custom_filters},
                      benefitPlanId: "{benefit_plan_id}",
                      status: "{status}",
                    }}
                  ) {{
                    clientMutationId
                    internalId
                  }}
                }}
            '''

            return self.query(
                query_str,
                headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
            )
        
        SocialProtectionConfig.enable_maker_checker_logic_enrollment = False
        successful_enrollments = []

        for i, case in enumerate(self.individual_enrollment_cases):
            expect_max_active_beneficiaries_exceeded = case.expected_summary["maxActiveBeneficiariesExceeded"]

            response = send_confirm_individual_enrollment_mutation(case.benefit_plan.uuid, case.status, case.custom_filters)
            self.assertResponseNoErrors(response)
            content = json.loads(response.content)
            id = content['data']['confirmIndividualEnrollment']['internalId']

            if expect_max_active_beneficiaries_exceeded:
                self.assert_mutation_error(id, _('mutation.max_active_beneficiaries_exceeded'), f"Case {i}")
            else:
                self.assert_mutation_success(id)
                client_mutation_id = content['data']['confirmIndividualEnrollment']['clientMutationId']
                wait_for_mutation(client_mutation_id)

                if case.benefit_plan not in successful_enrollments:
                    # Post enrollment check will only pass if no others have already changed enrollment numbers
                    num_already_enrolled = int(case.expected_summary['numberOfIndividualsAssignedToSelectedProgrammeAndStatus'])
                    num_uploaded = int(case.expected_summary['numberOfIndividualsToUpload'])
                    expected_total_post_enrollment = num_uploaded + num_already_enrolled

                    actual_post_enrollment = Individual.objects.all()\
                        .filter(beneficiary__benefit_plan_id=case.benefit_plan.uuid, beneficiary__status=case.status).count()

                    self.assertEqual(
                        expected_total_post_enrollment, actual_post_enrollment,
                        f'Expected test case {i} to have {expected_total_post_enrollment} {case.status} Individuals after enrollment, but got {actual_post_enrollment}'
                    )

                    successful_enrollments.append(case.benefit_plan)
