import json

from django.core.management.base import BaseCommand, CommandError

from main.ai_quality import run_persian_extraction_eval
from main.models import AIQualityEvaluation


class Command(BaseCommand):
    help = 'Run the deterministic Persian AI extraction quality suite.'

    def add_arguments(self, parser):
        parser.add_argument('--save', action='store_true', help='Persist the aggregate report.')
        parser.add_argument('--fail-under', type=float, default=None,
                            help='Exit unsuccessfully when pass rate is below this percentage.')

    def handle(self, *args, **options):
        report = run_persian_extraction_eval()
        if options['save']:
            AIQualityEvaluation.objects.create(
                suite_version=report['suite_version'], engine_version=report['engine_version'],
                total_cases=report['total_cases'], passed_cases=report['passed_cases'],
                pass_rate=report['pass_rate'], precision=report['precision'], recall=report['recall'],
                duration_ms=report['duration_ms'], report=report,
            )
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        threshold = options['fail_under']
        if threshold is not None and report['pass_rate'] < threshold:
            raise CommandError(
                f"AI quality pass rate {report['pass_rate']:.2f}% is below {threshold:.2f}%."
            )
        self.stdout.write(self.style.SUCCESS(
            f"{report['passed_cases']}/{report['total_cases']} cases passed; "
            f"precision={report['precision']:.2f}% recall={report['recall']:.2f}%"
        ))
