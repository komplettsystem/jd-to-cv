"""A CV line that is a long prose paragraph (a profile) is cited one sentence at a time, so the quote shown is the sentence the reason is about.
Bullets stay whole: a bullet is one claim written as several sentences."""
import match

PROFILE = ("I start with the customer's problem and how it connects to what we offer, then make the product seamless to adopt and valuable to use. "
           "Success shows in repeat use, new customers and revenue growth. A product now has two users: the people who decide to pay for it and fit it "
           "into their workflows, and the agents they point at it, which need a way in that is easy to connect and reliable to call. "
           "My own rule is to build what makes the product different, use standards where interoperability and scale matter, e.g. C2PA, and buy or partner for the rest.")


def chunks(md):
    return match.chunk_cv(md)


def test_a_long_prose_paragraph_is_one_chunk_per_sentence_in_the_same_section():
    cs = chunks("## Profile\n\n" + PROFILE + "\n")
    assert len(cs) == 4 and {c["section"] for c in cs} == {"Profile"}
    assert [c["id"] for c in cs] == ["c0", "c1", "c2", "c3"]
    assert cs[2]["text"].startswith("A product now has two users") and "easy to connect and reliable to call" in cs[2]["text"]
    assert " ".join(c["text"] for c in cs) == PROFILE       # nothing lost, nothing added


def test_an_abbreviation_does_not_end_a_sentence():
    cs = chunks("## Profile\n\n" + PROFILE + "\n")
    assert "e.g. C2PA" in cs[3]["text"]


def test_a_bullet_with_several_sentences_stays_one_chunk():
    bullet = "- " + PROFILE
    cs = chunks("## Experience\n\n" + bullet + "\n")
    assert len(cs) == 1 and cs[0]["text"] == PROFILE


def test_a_short_paragraph_is_not_split():
    cs = chunks("## Profile\n\nGerman native. English fluent since the early 2000s.\n")
    assert len(cs) == 1


def test_a_tiny_sentence_joins_its_neighbour_instead_of_standing_alone():
    text = ("Led the product teams for a platform where clients of every size bring in product catalogs, from onboarding to enrichment and the API layer. "
            "Billions of products. Self-service onboarding cut setup from days to minutes for most customers and removed the manual request queue.")
    cs = chunks("## Experience\n\n" + text + "\n")
    assert all(len(c["text"]) >= 40 for c in cs) and " ".join(c["text"] for c in cs) == text
