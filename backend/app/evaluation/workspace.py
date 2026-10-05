from app.audits.fixtures import DocumentKind, FixtureDocument, FixtureTemplate
from app.evaluation.dataset import UtilityCorpus, generate_utility_corpus


def utility_template(corpus: UtilityCorpus) -> FixtureTemplate:
    corpus = UtilityCorpus.model_validate(corpus.model_dump())
    if corpus.checksum != generate_utility_corpus(corpus.seed).checksum:
        raise ValueError("utility_corpus_drift")
    queries = {query.relevant_document_ids[0]: query for query in corpus.queries}
    return FixtureTemplate(
        generator_id="natural-utility-v1",
        pack_id="utility-v1",
        seed=corpus.seed,
        organizations=corpus.organizations,
        groups=corpus.groups,
        actors=corpus.actors,
        documents=tuple(
            FixtureDocument(
                id=document.id,
                organization_id=document.organization_id,
                kind=DocumentKind.USER
                if document.user_ids
                else DocumentKind.GROUP
                if document.group_ids
                else DocumentKind.ORGANIZATION,
                text=document.text,
                question=queries[document.id].question,
                canary_id=f"{document.id}-unused",
                canary="",
                visibility=document.visibility,
                user_ids=document.user_ids,
                group_ids=document.group_ids,
                control_actor_id=queries[document.id].actor_id,
            )
            for document in corpus.documents
        ),
        cases=(),
    )
