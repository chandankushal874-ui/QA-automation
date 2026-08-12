from src.core.dom_similarity import DOMSimilarityEngine


def test_exact_match_fast_path():
    engine = DOMSimilarityEngine()
    html_1 = "<html><body><div><p>Hello World</p><a href='/next'>Next</a></div></body></html>"
    # Content changes but structure is identical
    html_2 = "<html><body><div><p>Hello Python</p><a href='/next'>Next</a></div></body></html>"

    tid1, _sim1 = engine.get_template_id(html_1)
    tid2, sim2 = engine.get_template_id(html_2)

    assert tid1 == tid2
    assert sim2 == 1.0  # Exact structural paths match should trigger O(1) match


def test_jaccard_similarity_clustering():
    engine = DOMSimilarityEngine(similarity_threshold=0.9)

    html_base = """
    <html>
      <body>
        <div class="product-list">
          <div class="item"><h3>A</h3><p>Desc A</p></div>
          <div class="item"><h3>B</h3><p>Desc B</p></div>
        </div>
      </body>
    </html>
    """

    # Path sets will be identical because unique list paths are collapsed to sets
    html_similar = """
    <html>
      <body>
        <div class="product-list">
          <div class="item"><h3>C</h3><p>Desc C</p></div>
          <div class="item"><h3>D</h3><p>Desc D</p></div>
          <div class="item"><h3>E</h3><p>Desc E</p></div>
        </div>
      </body>
    </html>
    """

    # Structurally different layout
    html_dissimilar = """
    <html>
      <body>
        <form>
          <input type="text" name="username"/>
          <button type="submit">Submit</button>
        </form>
      </body>
    </html>
    """

    tid_base, _ = engine.get_template_id(html_base)
    tid_sim, sim_sim = engine.get_template_id(html_similar)
    tid_diff, _sim_diff = engine.get_template_id(html_dissimilar)

    assert tid_base == tid_sim
    assert sim_sim >= 0.9
    assert tid_base != tid_diff


def test_visit_limiting_and_gating():
    engine = DOMSimilarityEngine(visit_limit=2)
    html = "<html><body><div><p>Test</p></div></body></html>"

    tid, _ = engine.get_template_id(html)

    # First visit
    assert engine.record_visit(tid) == 1
    assert engine.should_gate_links(tid) is False

    # Second visit
    assert engine.record_visit(tid) == 2
    assert engine.should_gate_links(tid) is False

    # Third visit (exceeds limit)
    assert engine.record_visit(tid) == 3
    assert engine.should_gate_links(tid) is True
