import unittest

from main import _build_event_summary_payload
from vigil.core.models import Event
from vigil.core.news_relevance import filter_operational_articles, score_article_relevance


class SummarizerRelevanceTests(unittest.TestCase):
    def test_filter_operational_articles_discards_generic_blacklist_items(self):
        items = [
            {
                'title': 'Allerta rossa per neve e ghiaccio in Abruzzo',
                'description': 'Protezione civile e vigili del fuoco monitorano la situazione.',
                'url': 'https://www.ansa.it/meteo/notizie/2026/04/16/allerta-neve-abruzzo',
                'source': 'ANSA Meteo',
            },
            {
                'title': 'Astronauti verso Marte: la politica discute i costi',
                'description': 'Notizia generale senza impatto operativo.',
                'url': 'https://www.ansa.it/cronaca/notizie/2026/04/16/marte',
                'source': 'ANSA',
            },
        ]

        filtered = filter_operational_articles(items)

        self.assertEqual(len(filtered), 1)
        self.assertGreaterEqual(filtered[0]['relevance_score'], 0.4)
        self.assertNotIn('Astronauti', ' '.join(item['title'] for item in filtered))

    def test_build_event_summary_payload_ignores_low_relevance_articles(self):
        event = Event(
            id='summary-1',
            title='Bollettino DPC vento',
            type='storm',
            severity='orange',
            status='ATTENZIONE',
            region='Abruzzo',
            wind_kmh=82,
        )
        news_items = [
            {
                'title': 'Codice arancione per vento forte in Abruzzo',
                'description': 'Allerta meteo con raffiche oltre 80 km/h e protezione civile attiva.',
                'url': 'https://www.governo.it/it/notizia/allerta-vento-abruzzo/0001',
                'source': 'Governo',
                'media_type': 'article',
                'relevance_score': 1.0,
            },
            {
                'title': 'Astronauti e politica: nuova missione verso Marte',
                'description': 'Contenuto non operativo.',
                'url': 'https://www.ansa.it/politica/notizie/2026/04/16/marte',
                'source': 'ANSA',
                'media_type': 'article',
                'relevance_score': 0.0,
            },
        ]

        payload = _build_event_summary_payload(event, news_items, [], [])
        summary_text = f"{payload['summary']} {' '.join(payload.get('latest_headlines') or [])}".lower()

        self.assertIn('vento', summary_text)
        self.assertNotIn('astronaut', summary_text)
        self.assertNotIn('politica', summary_text)

    def test_score_article_relevance_is_zero_for_blacklisted_generic_title(self):
        score = score_article_relevance(
            'Sciopero e politica: cosa cambia per il lavoro',
            'Nessuna informazione operativa di protezione civile.',
            'https://example.com/news/politica',
            'Generic News',
        )
        self.assertEqual(score, 0.0)


if __name__ == '__main__':
    unittest.main()
