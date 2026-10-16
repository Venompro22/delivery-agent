"""🔖 Static files are versioned so browsers can cache them safely."""
import fileapp


def test_pages_link_versioned_files(client):
    html = client.get("/login").get_data(as_text=True)
    assert f"/static/pro.css?v={fileapp.ASSET_V}" in html
    assert len(fileapp.ASSET_V) == 10
